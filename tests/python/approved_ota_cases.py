# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# Focused owner-approved regressions; no private signing material is persisted.
import hashlib
import os
import uuid
import pysharp_signing
from config import StateStore
from main import Agent
from mqtt_transport import LoopbackTransport
from ota import OtaController
from runtime import Context, Runtime
from twin import Twin

SCRATCH_ROOT = "D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp"

PAYLOAD = b'''started = False
initialized = False
def on_start(ctx):
    global started
    started = True
def on_desired(ctx, patch):
    global initialized
    initialized = True
def on_tick(ctx):
    ctx.send_telemetry({"payloadVersion": ctx.payload_version})
def on_stop(ctx):
    pass
'''


class Clock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value


def environment(source=PAYLOAD):
    folder = os.path.join(SCRATCH_ROOT, "approved-ota-" + uuid.uuid4().hex)
    os.makedirs(folder, exist_ok=True)
    private, public = pysharp_signing.generate_keypair("ecdsa-p384-sha384")
    public_path = os.path.join(folder, "public.pem")
    with open(public_path, "w", encoding="utf-8") as f:
        f.write(public)
    payload_dir = os.path.join(folder, "seed")
    os.makedirs(payload_dir, exist_ok=True)
    seed = os.path.join(payload_dir, "thermostat.py")
    with open(seed, "wb") as f:
        f.write(source)
    config = {"registrationId": "offline-approved", "stateFile": os.path.join(folder, "state.json"),
              "payloadDir": payload_dir, "payloadPublicKeyPem": public_path,
              "allowDpsRegistration": False, "probationTicks": 1, "payloadWatchdogSec": 5}
    store = StateStore(config["stateFile"])
    store.data.update({"assignedHub": "loopback.invalid", "deviceId": "offline-approved"})
    transport = LoopbackTransport()
    transport.connect("loopback.invalid", "offline-approved")
    clock = Clock()
    twin = Twin(transport, clock=clock.now)
    ctx = Context("offline-approved", transport, twin, store.data)
    runtime = Runtime(ctx, seed, clock.now)
    ota = OtaController(config, store, runtime)
    return folder, private, config, store, transport, clock, twin, runtime, ota


def signed(private, data, version):
    return {"name": "thermostat", "version": version, "url": "https://artifact.invalid/payload.py",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "sig": pysharp_signing.sign("ecdsa-p384-sha384", private, data).hex()}


def submit(ota, private, data, version):
    ota.downloader = lambda url: data
    ota.submit(signed(private, data, version))
    ota.poll()


def test_ota_bootstrap_safe_state():
    folder, private, config, store, transport, clock, twin, runtime, ota = environment()
    try:
        assert ota.bootstrap() == "active"
        assert runtime.worker is not None and runtime.namespace is not None
        assert runtime.payload_path == store.data["current"]["path"]
        marker = os.path.join(folder, "rejected-executed")
        rejected = ("with open(" + repr(marker) + ", 'w') as f: f.write('executed')\n").encode("utf-8") + PAYLOAD
        # Both persisted current and lastGood point at a now-rejected file.
        with open(store.data["current"]["path"], "wb") as f:
            f.write(rejected)
        runtime.stop()
        assert ota.bootstrap() == "failed"
        assert runtime.stopped and runtime.worker is None and runtime.namespace is None
        assert not os.path.exists(marker)
        assert twin.pending["payload"]["status"] == "failed"
        assert twin.pending["payload"]["error"] == "rollback_failed"
        assert any(e["event"] == "payload_error" for e in runtime.ctx.events)
        # Agent must never fall through to Runtime.load on a rejected path.
        agent = Agent(config, store, transport, clock.now, lambda seconds: None)
        agent.runtime.payload_path = store.data["current"]["path"]
        agent.runtime.load = lambda: (_ for _ in ()).throw(AssertionError("unsafe load"))
        try:
            agent.start()
            assert transport.connected and agent.runtime.worker is None
            agent.step()
            assert not agent.runtime.restart()
            assert agent.runtime.worker is None and agent.runtime.namespace is None
            assert not os.path.exists(marker)
        finally:
            agent.stop()
        # Absence is a separate outcome, not an instruction to load a file.
        store.data.pop("current")
        store.data.pop("lastGood")
        runtime.payload_path = os.path.join(folder, "absent.py")
        assert ota.bootstrap() == "no_payload"
        # Corruption between seeding and restoration must be rejected before exec.
        runtime.payload_path = os.path.join(config["payloadDir"], "thermostat.py")
        from ota import atomic_write
        def corrupt_seed(path, data):
            atomic_write(path, rejected if path.endswith(".py") else data)
        ota.writer = corrupt_seed
        assert ota.bootstrap() == "failed"
        assert runtime.worker is None and runtime.namespace is None
        assert not os.path.exists(marker)
    finally:
        runtime.stop()


def test_ota_previous_accepted_rollback():
    folder, private, config, store, transport, clock, twin, runtime, ota = environment()
    try:
        assert ota.bootstrap() == "active"
        # Legacy state has no previousGood: report conflict without a reload.
        worker = runtime.worker
        assert "previousGood" not in store.data
        assert not ota.rollback()
        assert ota.error == "no_previous_good" and runtime.worker is worker
        assert store.data["current"]["version"] == 1
        methods = __import__("methods").Methods(transport, runtime)
        methods.message("$iothub/methods/POST/rollbackPayload/?$rid=legacy", "{}")
        assert "/409/" in transport.published[-1][0]
        submit(ota, private, PAYLOAD + b"# accepted version 2\n", 2)
        assert ota.status == "probation"
        clock.value += 1
        ota.tick_result(runtime.tick())
        assert ota.status == "running"
        assert store.data["lastGood"]["version"] == 2
        assert store.data["previousGood"]["version"] == 1
        persisted = StateStore(store.path)
        assert persisted.data["previousGood"]["version"] == 1
        methods.message("$iothub/methods/POST/rollbackPayload/?$rid=accepted", "{}")
        assert "/200/" in transport.published[-1][0]
        assert runtime.ctx.payload_version == store.data["current"]["version"] == 1
        assert store.data["lastGood"]["version"] == 1
        # Explicit rollback during probation targets lastGood, not history.
        submit(ota, private, PAYLOAD + b"# probation version 3\n", 3)
        assert ota.status == "probation" and ota.rollback()
        assert store.data["current"]["version"] == 1
        # Automatic recovery always uses lastGood even when history exists.
        submit(ota, private, PAYLOAD + b"# probation version 4\n", 4)
        assert ota.rollback("tick_failed")
        assert store.data["current"]["version"] == 1
    finally:
        runtime.stop()


class ReadFailureWorker:
    # Deterministic transport failure; actual module execution remains in worker.
    def __init__(self, worker):
        self.worker = worker
        self.error = None
        self.fail_tick = True
    def call(self, name, patch=None):
        if name == "on_tick" and self.fail_tick:
            self.fail_tick = False
            self.error = "response_read_failure"
            return False
        result = self.worker.call(name, patch)
        self.error = self.worker.error
        return result
    def replacement(self):
        return ReadFailureWorker(self.worker.replacement())
    def close(self):
        self.worker.close()


def watchdog_diagnostic(label, ota, runtime, store, clock, healthy=None, hung=None):
    def alive(worker):
        if worker is None:
            return None
        while hasattr(worker, "worker"):
            worker = worker.worker
        return worker.process.poll() is None
    heartbeat_age = None
    healthy_exit = None
    if healthy is not None:
        import time
        healthy_exit = healthy.process.poll()
        try:
            with open(healthy.heartbeat_path, encoding="ascii") as stream:
                heartbeat_age = time.time() - float(stream.read())
        except (OSError, ValueError):
            heartbeat_age = "unreadable"
    return str({"assertion": label, "healthyExitCode": healthy_exit, "heartbeatAge": heartbeat_age, "status": ota.status, "error": ota.error,
                "lastError": runtime.last_error, "restarts": runtime.worker_restarts,
                "errors": runtime.errors, "current": store.data.get("current", {}).get("version"),
                "lastGood": store.data.get("lastGood", {}).get("version"), "clock": clock.value,
                "workerAlive": alive(runtime.worker), "healthyAlive": alive(healthy), "hungAlive": alive(hung)})

def test_ota_desired_tick_watchdog_rollback():
    import time
    folder, private, config, store, transport, clock, twin, runtime, ota = environment()
    healthy = None
    hung_worker = None
    def diagnostic(label):
        return watchdog_diagnostic(label, ota, runtime, store, clock, healthy, hung_worker)
    try:
        ota.config["workerHeartbeatTimeoutSec"] = 2
        assert ota.bootstrap() == "active", diagnostic("ota.bootstrap() == \"active\"")
        twin.on_desired = runtime.desired_changed
        # This OTA regression uses a fake logical clock. Do not turn its
        # precondition into a real-time scheduler stress test by sleeping past
        # the minimum heartbeat deadline. Idle heartbeat liveness is covered
        # separately by test_worker_heartbeat_idle. Keep actual worker callbacks
        # and the real payload-hang watchdog here, without changing any budget.
        healthy = runtime.worker
        assert healthy.process.poll() is None, diagnostic("healthy.process.poll() is None")
        assert runtime.tick() and runtime.worker is healthy, diagnostic("runtime.tick() and runtime.worker is healthy")
        assert runtime.worker_restarts == 0 and runtime.errors == 0, diagnostic("runtime.worker_restarts == 0 and runtime.errors == 0")
        hanging = PAYLOAD.replace(b'    ctx.send_telemetry({"payloadVersion": ctx.payload_version})', b'    while True:\n        pass')
        ota.downloader = lambda url: hanging
        twin.apply({"$version": 1, "payload": signed(private, hanging, 2)})
        ota.poll()
        assert ota.status == "probation", diagnostic("ota.status == \"probation\"")
        hung_worker = runtime.worker
        clock.value += 1
        ota.tick_result(runtime.tick())
        assert ota.status == "rolledBack" and ota.error == "watchdog_timeout", diagnostic("ota.status == \"rolledBack\" and ota.error == \"watchdog_timeout\"")
        assert hung_worker.process.poll() is not None, diagnostic("hung_worker.process.poll() is not None")
        assert store.data["current"]["version"] == store.data["lastGood"]["version"] == 1, diagnostic("store.data[\"current\"][\"version\"] == store.data[\"lastGood\"][\"version\"] == 1")
        assert runtime.ctx.payload_version == 1 and runtime.errors == 1, diagnostic("runtime.ctx.payload_version == 1 and runtime.errors == 1")
        assert runtime.worker_restarts == 1, diagnostic("runtime.worker_restarts == 1")
        assert any(e.get("reason") == "payload_watchdog_timeout" for e in runtime.ctx.events), diagnostic("any(e.get(\"reason\") == \"payload_watchdog_timeout\" for e in runtime.ctx.events)")
        before = len(transport.published)
        clock.value += 1
        assert runtime.tick() and len(transport.published) > before, diagnostic("runtime.tick() and len(transport.published) > before")
        # Repeated incomplete/read failures are infrastructure, not evidence
        # against an OTA artifact; probation and lastGood must be retained.
        submit(ota, private, PAYLOAD + b"# infrastructure candidate\n", 3)
        runtime.worker = ReadFailureWorker(runtime.worker)
        clock.value += 1
        ota.tick_result(runtime.tick())
        assert ota.status == "probation", diagnostic("ota.status == \"probation\"")
        assert runtime.last_error == "response_read_failure", diagnostic("runtime.last_error == \"response_read_failure\"")
        assert store.data["current"]["version"] == 3 and store.data["lastGood"]["version"] == 1, diagnostic("store.data[\"current\"][\"version\"] == 3 and store.data[\"lastGood\"][\"version\"] == 1")
        assert runtime.errors == 1, diagnostic("runtime.errors == 1")
        clock.value += 1
        ota.tick_result(runtime.tick())
        assert ota.status == "running" and store.data["lastGood"]["version"] == 3, diagnostic("ota.status == \"running\" and store.data[\"lastGood\"][\"version\"] == 3")
    finally:
        runtime.stop()


def test_worker_replacement_initialization():
    source = b'''starts = 0
settings = {}
def record(ctx, name):
    ctx.state["lifecycle"] = ctx.state.get("lifecycle", []) + [name]
def on_start(ctx):
    global starts
    starts += 1
    record(ctx, "start")
def on_desired(ctx, patch):
    settings.update(patch)
    record(ctx, "desired")
def on_tick(ctx):
    if starts != 1 or settings.get("targetTemperature") != ctx.desired["targetTemperature"] or settings.get("thresholds") != ctx.desired["thresholds"]:
        raise ValueError("initialization required")
    record(ctx, "tick")
    ctx.state["successfulTicks"] = ctx.state.get("successfulTicks", 0) + 1
    ctx.send_telemetry({"initialized": True})
def on_stop(ctx):
    pass
'''
    folder, private, config, store, transport, clock, twin, runtime, ota = environment(source)
    try:
        assert ota.bootstrap() == "active"
        twin.desired["targetTemperature"] = 25
        twin.desired["thresholds"]["high"] = 30
        assert runtime.call("on_desired", runtime.ctx.desired)
        assert runtime.tick()
        committed = runtime.ctx.state["successfulTicks"]
        old = runtime.worker
        old.process.kill()
        old.process.wait(timeout=5)
        clock.value += 1
        assert runtime.tick()
        assert runtime.worker is not old and runtime.worker.process.poll() is None
        assert runtime.ctx.state["lifecycle"][-3:] == ["start", "desired", "tick"]
        assert runtime.ctx.state["successfulTicks"] == committed + 1
        assert runtime.worker_restarts == 1 and runtime.errors == 0
        # Recovery of on_desired must initialize from full desired before
        # retrying the partial patch which originally failed.
        runtime.worker.process.kill()
        runtime.worker.process.wait(timeout=5)
        twin.desired["targetTemperature"] = 27
        assert runtime.call("on_desired", {"targetTemperature": 27})
        assert runtime.ctx.state["lifecycle"][-3:] == ["start", "desired", "desired"]
        clock.value += 1
        assert runtime.tick() and runtime.errors == 0 and runtime.worker_restarts == 2
        runtime.stop()
        # Isolated recovery seams assert that start is never doubled and a
        # failed initialization does not permit the failed callback retry.
        class ProbeWorker:
            def __init__(self, error=None, replacement=None):
                self.error = error
                self.initial_error = error
                self.next = replacement
                self.calls = []
            def call(self, name, patch=None):
                self.calls.append(name)
                self.error = self.initial_error
                return self.error is None
            def replacement(self):
                return self.next
            def close(self):
                pass
        replacement = ProbeWorker()
        runtime.worker = ProbeWorker("worker_exited", replacement)
        assert runtime.call("on_start")
        assert replacement.calls == ["on_start", "on_desired"]
        failed = ProbeWorker("payload_exception")
        runtime.worker = ProbeWorker("worker_exited", failed)
        assert not runtime.call("on_tick")
        assert failed.calls == ["on_start"]
        assert runtime.last_error == "payload_exception" and runtime.errors == 1
        runtime.worker = None
    finally:
        runtime.stop()


APPROVED_OTA_TESTS = [test_ota_bootstrap_safe_state, test_ota_previous_accepted_rollback,
                      test_ota_desired_tick_watchdog_rollback, test_worker_replacement_initialization]
