# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# Cases registered by the existing run_tests.py / xUnit harness.
import hashlib
import http.client
import json
import os

# Legacy environment names remain accepted for one release.
if not os.environ.get('PYGATE_SOAK_AGENT') and os.environ.get('FORGE_SOAK_AGENT'):
    os.environ['PYGATE_SOAK_AGENT'] = os.environ['FORGE_SOAK_AGENT']
if not os.environ.get('PYGATE_PAHO_SITE') and os.environ.get('FORGE_PAHO_SITE'):
    os.environ['PYGATE_PAHO_SITE'] = os.environ['FORGE_PAHO_SITE']
if not os.environ.get('PYGATE_TEST_KEY') and os.environ.get('FORGE_TEST_KEY'):
    os.environ['PYGATE_TEST_KEY'] = os.environ['FORGE_TEST_KEY']
if not os.environ.get('PYGATE_TEST_SCRATCH') and os.environ.get('FORGE_DEVICE_TEST_SCRATCH'):
    os.environ['PYGATE_TEST_SCRATCH'] = os.environ['FORGE_DEVICE_TEST_SCRATCH']
if not os.environ.get('PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC') and os.environ.get('FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC'):
    os.environ['PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC'] = os.environ['FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC']
if not os.environ.get('PYGATE_TEST_CERT') and os.environ.get('FORGE_TEST_CERT'):
    os.environ['PYGATE_TEST_CERT'] = os.environ['FORGE_TEST_CERT']
if not os.environ.get('PYGATE_ADR_FIXTURE_ROOT') and os.environ.get('FORGE_ADR_FIXTURE_ROOT'):
    os.environ['PYGATE_ADR_FIXTURE_ROOT'] = os.environ['FORGE_ADR_FIXTURE_ROOT']
import sys
import tempfile
import time
import pysharp_signing
from config import StateStore
from mqtt_transport import LoopbackTransport
from runtime import Context, Runtime
from twin import Twin
from methods import Methods
from ota import OtaController, OtaError, download_https

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))), "src")
sys.path.insert(0, os.path.join(ROOT, "tools"))
from make_artifact import make_manifest


class TestClock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


def environment():
    parent = os.environ.get("PYGATE_TEST_SCRATCH", "D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp/step04")
    os.makedirs(parent, exist_ok=True)
    folder = tempfile.mkdtemp(prefix="ota-case-", dir=parent)
    private, public = pysharp_signing.generate_keypair("ecdsa-p384-sha384")
    with open(os.path.join(folder, "public.pem"), "w") as f:
        f.write(public)
    store = StateStore(os.path.join(folder, "state.json"))
    transport = LoopbackTransport()
    transport.connect("loopback.invalid", "ota-test")
    clock = TestClock()
    twin = Twin(transport, clock=clock.now)
    transport.on_message = twin.message
    ctx = Context("ota-test", transport, twin, store.data)
    runtime = Runtime(ctx, os.path.join(ROOT, "payloads", "thermostat.py"), clock.now)
    config = {"payloadPublicKeyPem": os.path.join(folder, "public.pem"), "probationTicks": 4, "payloadWatchdogSec": 2}
    ota = OtaController(config, store, runtime, downloader=lambda url: read_fixture(url.split("/")[-1].split("?")[0]))
    ota.bootstrap()
    runtime.twin = twin
    twin.on_desired = runtime.desired_changed
    return folder, private, store, transport, clock, twin, runtime, ota


def read_fixture(name):
    with open(os.path.join(ROOT, "payloads", name), "rb") as f:
        return f.read()


def manifest(private, version=2, fixture="thermostat_v2.py"):
    return make_manifest(os.path.join(ROOT, "payloads", fixture), "thermostat", version, "https://artifact.invalid/" + fixture + "?token=SECRET-SENTINEL", private)


def ticks(clock, runtime, ota, count=5):
    for i in range(count):
        clock.sleep(1)
        ota.tick_result(runtime.tick())


def test_ota_good_idempotent_downgrade():
    folder, key, store, transport, clock, twin, runtime, ota = environment()
    try:
        m = manifest(key)
        ota.submit(m)
        ota.poll()
        assert ota.status == "probation", (ota.status, ota.error)
        assert store.data["lastGood"]["version"] == 1
        ticks(clock, runtime, ota)
        assert ota.status == "running" and store.data["lastGood"]["version"] == 2, (ota.status, ota.error)
        assert ota.history[-5:] == ["downloading", "verifying", "staged", "probation", "running"]
        events = [json.loads(p[1]) for p in transport.published if p[0].startswith("devices/")]
        assert any(e.get("humidity") is not None and e["payloadVersion"] == 2 for e in events)
        count = len(ota.history)
        ota.submit(m)
        ota.poll()
        assert len(ota.history) == count
        lower = manifest(key, 1, "thermostat.py")
        ota.submit(lower)
        ota.poll()
        ticks(clock, runtime, ota)
        assert store.data["current"]["version"] == 1, (ota.status, ota.error)
        assert store.data["current"]["downgrade"], (ota.status, ota.error)
        assert "SECRET-SENTINEL" not in json.dumps(twin.pending)
        assert "SECRET-SENTINEL" not in json.dumps(store.data)
    finally:
        runtime.stop()


def test_ota_integrity_and_syntax_rejection():
    folder, key, store, t, clock, twin, runtime, ota = environment()
    try:
        cases = []
        cases.append((manifest(key, 3, "thermostat_v3_syntax.py"), "syntax_error"))
        bad = manifest(key, 6, "thermostat_v6_hash.py")
        bad["sha256"] = "0" * 64
        cases.append((bad, "hash_mismatch"))
        bad = manifest(key, 7, "thermostat_v7_signature.py")
        bad["sig"] = "0" * 192
        cases.append((bad, "signature_mismatch"))
        other, public = pysharp_signing.generate_keypair("ecdsa-p384-sha384")
        cases.append((manifest(other, 8, "thermostat_v8_other_key.py"), "signature_mismatch"))
        bad = manifest(key, 9)
        bad["size"] += 1
        cases.append((bad, "size_mismatch"))
        for m, reason in cases:
            ota.submit(m)
            ota.poll()
            assert ota.status == "failed", ota.error
            assert ota.error == reason, ota.error
            assert store.data["current"]["version"] == 1
            before = len(t.published)
            clock.sleep(1)
            runtime.tick()
            assert len(t.published) > before
    finally:
        runtime.stop()


def test_ota_runtime_error_and_watchdog():
    folder, key, store, t, clock, twin, runtime, ota = environment()
    try:
        ota.submit(manifest(key, 4, "thermostat_v4_runtime.py"))
        ota.poll()
        ticks(clock, runtime, ota, 3)
        assert ota.status == "rolledBack" and ota.error == "payload_exception"
        assert runtime.ctx.payload_version == 1
        assert store.data["lastGood"]["version"] == 1
        before = len(t.published)
        clock.sleep(1)
        runtime.tick()
        assert len(t.published) > before
        # Use the configured watchdog for validation and both hang attempts.
        # A sub-second value made PySharp startup/IPC overhead look like an
        # entry-point failure when the complete suite loaded the machine.
        watchdog = float(ota.config["payloadWatchdogSec"])
        ota.submit(manifest(key, 5, "thermostat_v5_hang.py"))
        ota.poll()
        assert ota.status == "probation", ota.error
        hung_worker = runtime.worker
        start = time.monotonic()
        ticks(clock, runtime, ota, 1)
        elapsed = time.monotonic() - start
        # Runtime makes one fresh-worker retry, so two watchdog periods are
        # expected. Keep the test bound proportional to the configured value.
        assert elapsed < watchdog * 4 + 2, (elapsed, watchdog)
        print("WATCHDOG_ELAPSED seconds=" + str(elapsed) + " configured=" + str(watchdog))
        # Two tick watchdog expiries in fresh workers identify a payload hang;
        # automatic rollback restores lastGood and its healthy telemetry.
        assert ota.status == "rolledBack" and ota.error == "watchdog_timeout"
        assert runtime.errors == 2  # Genuine runtime exception plus payload hang.
        assert hung_worker.process.poll() is not None
        assert runtime.ctx.payload_version == 1
        assert store.data["current"]["version"] == store.data["lastGood"]["version"] == 1
        assert any(e.get("reason") == "payload_watchdog_timeout" for e in runtime.ctx.events)
    finally:
        runtime.stop()


def test_ota_restart_probation_and_catchup():
    folder, key, store, t, clock, twin, runtime, ota = environment()
    try:
        m = manifest(key)
        # Desired survives offline independently of the old process.
        t.desired = {"$version": 2, "payload": m}
        twin.connected()
        for i in range(4):
            t.poll()
        assert ota.pending["version"] == 2
        ota.poll()
        assert ota.status == "probation"
        runtime.stop()
        recovered = StateStore(store.path)
        runtime.ctx.state = recovered.data
        restarted = OtaController(ota.config, recovered, runtime, ota.downloader)
        assert restarted.bootstrap() == "active"
        assert restarted.status == "rolledBack"
        assert restarted.error == "restart_during_probation"
        assert recovered.data["current"]["version"] == 1
        restarted.submit(m)
        restarted.poll()
        ticks(clock, runtime, restarted)
        assert restarted.status == "running" and recovered.data["current"]["version"] == 2
        runtime.stop()
        normal = OtaController(ota.config, recovered, runtime, ota.downloader)
        assert normal.bootstrap() == "active"
        assert runtime.ctx.payload_version == 2
    finally:
        runtime.stop()


def test_ota_storage_and_latest_wins():
    folder, key, store, t, clock, twin, runtime, ota = environment()
    try:
        def disk_full(path, data):
            raise OSError("DISK-SECRET")
        ota.writer = disk_full
        ota.submit(manifest(key))
        ota.poll()
        assert ota.status == "failed" and store.data["current"]["version"] == 1
        assert "DISK-SECRET" not in json.dumps(twin.pending)
        from ota import atomic_write
        ota.writer = atomic_write
        newer = manifest(key, 10)
        def delayed_download(url):
            ota.submit(newer)
            return read_fixture("thermostat_v2.py")
        ota.downloader = delayed_download
        ota.submit(manifest(key, 9))
        ota.poll()
        assert store.data["current"]["version"] == 1
        ota.downloader = lambda url: read_fixture("thermostat_v2.py")
        ota.poll()
        ticks(clock, runtime, ota)
        assert store.data["current"]["version"] == 10
        # Keep three versions; archive, never outright delete generated files.
        for version in [11, 12, 13]:
            ota.submit(manifest(key, version))
            ota.poll()
            ticks(clock, runtime, ota)
        ota.prune()
        files = [n for n in os.listdir(os.path.join(ota.root, "thermostat")) if n.endswith(".py")]
        assert len(files) == 3, (files, ota.status, ota.error)
        assert store.data["current"]["version"] == 13, (ota.status, ota.error)
        # Pruned versions are archived next to the artifacts (same volume), never deleted.
        archive = os.path.join(ota.root, "thermostat", "archive")
        archived = [n for _, _, names in os.walk(archive) for n in names]
        assert any(n.endswith(".py") for n in archived), archived
        # A failing prune is housekeeping: the accepted update must stay accepted.
        def broken_prune():
            raise OSError("prune_broken")
        ota.prune = broken_prune
        ota.submit(manifest(key, 14))
        ota.poll()
        ticks(clock, runtime, ota)
        assert store.data["current"]["version"] == 14 and store.data["lastGood"]["version"] == 14, (ota.status, ota.error)
        assert ota.status == "running", (ota.status, ota.error)
    finally:
        runtime.stop()


def test_ota_methods_and_timeout():
    folder, key, store, t, clock, twin, runtime, ota = environment()
    try:
        m = manifest(key)
        m["probationTicks"] = 1
        status, result = ota.apply_sync(m, 5)
        assert status == 200, result
        methods = Methods(t, runtime)
        methods.message("$iothub/methods/POST/rollbackPayload/?$rid=42", "{}")
        assert "/200/" in t.published[-1][0]
        # Explicit rollback now restores the version accepted before version 2.
        assert runtime.ctx.payload_version == 1
        assert store.data["lastGood"]["version"] == 1
        newer = manifest(key, 3)
        newer["probationTicks"] = 100
        methods.message("$iothub/methods/POST/applyPayload/?$rid=43", json.dumps(dict(newer, sig="0" * 192)))
        assert "/409/" in t.published[-1][0]
        status, result = ota.apply_sync(newer, 0.3)
        assert status == 409
        assert ota.status == "rolledBack" and ota.error == "method_timeout"
        # The deadline also covers slow download/validation done before probation starts.
        import time
        slow = manifest(key, 4)
        slow["probationTicks"] = 1
        original = ota.downloader
        def slow_download(url):
            time.sleep(1.0)
            return original(url)
        ota.downloader = slow_download
        started = time.monotonic()
        status, result = ota.apply_sync(slow, 0.5)
        assert status == 409, (status, result)
        assert store.data["current"].get("version") != 4, store.data["current"]
    finally:
        runtime.stop()


class LocalFileServer:
    def __init__(self):
        import socket
        import threading
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stopped = False
        self.retries = 0
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stopped:
            client = None
            try:
                client, address = self.listener.accept()
                client.settimeout(2)
                request = client.recv(8192).decode("ascii")
                path = request.split(" ")[1].split("?")[0]
                status = 200
                body = read_fixture("thermostat_v2.py")
                if path == "/retry":
                    self.retries += 1
                    if self.retries == 1:
                        status = 503
                elif path == "/redirect":
                    status = 302
                elif path == "/big":
                    body = b"x" * 100
                elif path == "/slow":
                    time.sleep(0.4)
                header = "HTTP/1.1 " + str(status) + " Test\r\nContent-Length: " + str(len(body)) + "\r\nConnection: close\r\n\r\n"
                client.sendall(header.encode("ascii") + body)
            except Exception:
                pass
            finally:
                if client:
                    client.close()

    def close(self):
        self.stopped = True
        self.listener.close()
        self.thread.join(3)


def test_ota_download_local_http():
    # The production downloader rejects HTTP. Only this injected factory maps an
    # HTTPS-shaped URL to the xUnit-owned plain HTTP loopback server.
    server = LocalFileServer()
    try:
        check_download_local_http(server.port)
    finally:
        server.close()


def check_download_local_http(port):
    factory = lambda host, ignored_port, timeout: http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    body = download_https("https://fixture.invalid/thermostat_v2.py?token=SECRET", connection_factory=factory)
    assert body == read_fixture("thermostat_v2.py")
    delays = []
    assert download_https("https://fixture.invalid/retry", sleep=lambda n: delays.append(n), connection_factory=factory)
    assert delays == [1]
    for url, cap, expected in [("https://fixture.invalid/big", 10, "size_cap"), ("https://fixture.invalid/redirect", 262144, "http_status"), ("http://fixture.invalid/x", 262144, "https_required")]:
        try:
            download_https(url, cap=cap, connection_factory=factory)
            assert False
        except OtaError as exc:
            assert str(exc) == expected
    try:
        download_https("https://fixture.invalid/slow", timeout=0.1, attempts=1, connection_factory=factory)
        assert False
    except OtaError as exc:
        assert str(exc) == "download_failed"


OTA_TESTS = [test_ota_good_idempotent_downgrade, test_ota_integrity_and_syntax_rejection, test_ota_runtime_error_and_watchdog, test_ota_restart_probation_and_catchup, test_ota_storage_and_latest_wins, test_ota_methods_and_timeout, test_ota_download_local_http]
