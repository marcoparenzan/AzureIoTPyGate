# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
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
import json

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))), "src")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "payloads"))
from mqtt_transport import LoopbackTransport, backoff, query
from dps import Provisioner
from twin import Twin
from runtime import Context, Runtime, JsonEncoder
from methods import Methods
from config import StateStore, load_config
from main import Agent
from thermostat import alarm_transition

SCRATCH = os.environ.get("PYGATE_TEST_SCRATCH", "D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp/worker-recovery/device-tests")
os.makedirs(SCRATCH, exist_ok=True)


class Clock:
    def __init__(self):
        self.value = 0.0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


def config():
    return {"registrationId": "offline-test", "idScope": "offline-scope", "globalEndpoint": "loopback.invalid", "cert": "unused.pem", "key": "unused.pem", "stateFile": os.path.join(SCRATCH, "state.json"), "payloadDir": os.path.join(ROOT, "payloads"), "allowDpsRegistration": True}


def drain(transport):
    for i in range(20):
        transport.poll()


def setup_runtime():
    clock = Clock()
    transport = LoopbackTransport()
    twin = Twin(transport, clock=clock.now)
    ctx = Context("offline-test", transport, twin, {})
    runtime = Runtime(ctx, os.path.join(ROOT, "payloads", "thermostat.py"), clock.now)
    twin.on_desired = runtime.desired_changed
    transport.on_message = twin.message
    transport.on_connect = twin.connected
    transport.connect("loopback.invalid", "offline-test")
    runtime.load()
    drain(transport)
    return clock, transport, twin, ctx, runtime


def test_dps_happy_retry_cache():
    clock = Clock()
    transport = LoopbackTransport()
    transport.dps_responses = [("$dps/registrations/res/202/?$rid=RID&retry-after=3", {"operationId": "op/a", "status": "assigning"}), ("$dps/registrations/res/200/?$rid=RID", {"operationId": "op/a", "status": "assigned", "registrationState": {"assignedHub": "hub.invalid", "deviceId": "assigned-test"}})]
    state = {}
    provisioner = Provisioner(config(), state, transport, clock.now, clock.sleep)
    assert provisioner.register() == ("hub.invalid", "assigned-test")
    assert clock.value >= 3
    assert "operationId=op%2Fa" in transport.published[1][0]
    count = len(transport.published)
    assert provisioner.register() == ("hub.invalid", "assigned-test")
    assert len(transport.published) == count
    provisioner.clear_assignment()
    assert "assignedHub" not in state and "deviceId" not in state


def test_dps_throttle_timeout_permission():
    clock = Clock()
    t = LoopbackTransport()
    t.dps_responses = [("$dps/registrations/res/429/?$rid=RID&retry-after=2", {}), ("$dps/registrations/res/200/?$rid=RID", {"status": "assigned", "registrationState": {"assignedHub": "h.invalid", "deviceId": "d"}})]
    p = Provisioner(config(), {}, t, clock.now, clock.sleep)
    p.register()
    assert clock.value >= 2
    t = LoopbackTransport()
    try:
        Provisioner(config(), {}, t, clock.now, clock.sleep).register(timeout=1)
        assert False
    except TimeoutError:
        pass
    c = config()
    c["allowDpsRegistration"] = False
    try:
        Provisioner(c, {}, t, clock.now, clock.sleep).register()
        assert False
    except PermissionError:
        pass


def test_desired_convergence_stale_validation():
    clock, t, twin, ctx, runtime = setup_runtime()
    t.inject_desired({"$version": 2, "targetTemperature": 24, "telemetryIntervalSec": 0, "thresholds": {"high": 29}})
    drain(t)
    assert twin.desired["targetTemperature"] == 24
    assert twin.desired["telemetryIntervalSec"] == 1
    twin.flush()
    drain(t)
    assert t.reported["applied"]["targetTemperature"] == 24
    t.inject_desired({"$version": 1, "targetTemperature": 99})
    drain(t)
    assert twin.desired["targetTemperature"] == 24
    snapshot = ctx.desired
    snapshot["targetTemperature"] = 100
    assert twin.desired["targetTemperature"] == 24
    try:
        twin.apply({"$version": 3, "thresholds": {"high": 10, "low": 20}})
        assert False
    except ValueError:
        pass
    assert twin.version == 2
    twin.apply({"$version": 4, "telemetryIntervalSec": 10000})
    assert twin.desired["telemetryIntervalSec"] == 3600
    try:
        twin.apply({"$version": 5, "targetTemperature": float("nan")})
        assert False
    except ValueError:
        pass
    assert twin.version == 4
    twin.apply({"$version": 6, "secret": "SECRET-SENTINEL", "payload": {"url": "https://invalid/?token=SECRET-SENTINEL"}})
    clock.sleep(5)
    twin.flush()
    drain(t)
    assert "SECRET-SENTINEL" not in json.dumps(t.reported)
    ctx.log("SECRET-SENTINEL")
    assert "SECRET-SENTINEL" not in json.dumps(runtime.diagnostics())


def test_alarm_hysteresis():
    thresholds = {"high": 26, "low": 16, "hysteresis": 0.5}
    rows = [("normal", 26, "normal"), ("normal", 26.1, "high"), ("high", 25.5, "high"), ("high", 25.49, "normal"), ("normal", 16, "normal"), ("normal", 15.9, "low"), ("low", 16.5, "low"), ("low", 16.51, "normal"), ("high", 15, "low"), ("low", 27, "high")]
    for old, temperature, expected in rows:
        assert alarm_transition(old, temperature, thresholds) == expected
    clock, t, twin, ctx, runtime = setup_runtime()
    ctx.state["temperature"] = 30
    runtime.namespace["on_tick"](ctx)
    events = [item for item in t.published if item[0].startswith("devices/")]
    body = json.loads(events[-1][1])
    assert body["alarm"]["type"] == "high"
    assert query("topic?" + events[-1][0].split("/messages/events/")[1])["alarm"] == "high"
    assert "alarmState" in twin.pending
    assert body["payloadVersion"] == 1


def test_report_throttle_coalesce_temperature():
    clock, t, twin, ctx, runtime = setup_runtime()
    twin.pending = {}
    twin.report({"sample": 1, "currentTemperature": 21})
    twin.flush()
    drain(t)
    twin.report({"sample": 2})
    twin.report({"sample": 3, "currentTemperature": 22})
    clock.sleep(4.99)
    twin.flush()
    assert t.reported["sample"] == 1
    clock.sleep(0.01)
    twin.flush()
    drain(t)
    assert t.reported["sample"] == 3
    assert t.reported["currentTemperature"] == 21
    clock.sleep(25)
    twin.flush()
    drain(t)
    assert t.reported["currentTemperature"] == 22
    count = len(t.published)
    twin.report({"sample": 3})
    clock.sleep(5)
    twin.flush()
    assert len(t.published) == count


def test_payload_exception_containment():
    clock, t, twin, ctx, runtime = setup_runtime()
    def broken(context):
        raise RuntimeError("SECRET-SENTINEL-never-log")
    runtime.namespace["on_tick"] = broken
    runtime.tick()
    assert runtime.errors == 1
    clock.sleep(1)
    runtime.tick()
    assert runtime.errors == 2
    assert "SECRET-SENTINEL" not in json.dumps(runtime.diagnostics())
    assert "SECRET-SENTINEL" not in json.dumps(twin.pending)
    assert t.connected


def test_methods():
    clock, t, twin, ctx, runtime = setup_runtime()
    methods = Methods(t, runtime)
    t.on_message = methods.message
    for name, status in [("ping", 200), ("getDiagnostics", 200), ("restartPayload", 200), ("applyPayload", 501), ("rollbackPayload", 501), ("unknown", 404)]:
        t.inject_method(name, "rid/1")
        drain(t)
        assert t.published[-1][0] == "$iothub/methods/res/" + str(status) + "/?$rid=rid%2F1"
    methods.message("$iothub/methods/POST/ping/?$rid=bad", "not-json")
    assert "/400/" in t.published[-1][0]
    t.inject_method("rebootAgent")
    drain(t)
    assert runtime.reboot_requested


def test_reconnect_backoff_reprovision():
    assert backoff(0, lambda: 0) == 1
    assert backoff(0, lambda: 1) == 2
    assert backoff(20, lambda: 1) == 60
    assert backoff(1, lambda: 0) > backoff(0, lambda: 0)
    clock = Clock()
    store = StateStore(os.path.join(SCRATCH, "reprovision-state.json"))
    store.data = {"assignedHub": "old.invalid", "deviceId": "old-device"}
    t = LoopbackTransport()
    agent = Agent(config(), store, t, clock.now, clock.sleep)
    try:
        agent.start()
        t.dps_responses = [("$dps/registrations/res/200/?$rid=RID", {"status": "assigned", "registrationState": {"assignedHub": "new.invalid", "deviceId": "new-device"}})]
        t.force_disconnect("unauthorized")
        agent.step()
        assert store.data["assignedHub"] == "new.invalid"
        assert agent.ctx.device_id == "new-device"
        t.force_disconnect()
        clock.sleep(2)
        agent.step()
        assert t.connected
    finally:
        agent.stop()


def test_state_encoder_and_loader():
    path = os.path.join(SCRATCH, "persist.json")
    store = StateStore(path)
    store.data = {"assignedHub": "hub.invalid", "deviceId": "d", "counter": 12}
    store.save()
    assert StateStore(path).data["counter"] == 12
    payload, props = JsonEncoder().encode({"a": 1}, {"alarm": "high", "custom": "a & b"})
    assert json.loads(payload)["a"] == 1
    assert query("topic?" + props)["$.ct"] == "application/json"
    assert query("topic?" + props)["custom"] == "a & b"
    clock, t, twin, ctx, runtime = setup_runtime()
    bad = os.path.join(SCRATCH, "bad-payload.py")
    with open(bad, "w") as f:
        f.write("value = 1")
    runtime.payload_path = bad
    try:
        runtime.load()
        assert False
    except ValueError:
        pass
    assert runtime.namespace is not None


def test_ten_minutes_virtual_disconnect():
    clock = Clock()
    store = StateStore(os.path.join(SCRATCH, "virtual-long-state.json"))
    store.data = {"assignedHub": "hub.invalid", "deviceId": "offline-test"}
    t = LoopbackTransport()
    agent = Agent(config(), store, t, clock.now, clock.sleep)
    agent.start()
    for i in range(602):
        if i == 300:
            t.force_disconnect()
        agent.step()
        clock.sleep(1)
    agent.stop()
    assert clock.now() >= 600
    assert agent.runtime.errors == 0
    assert len([p for p in t.published if p[0].startswith("devices/")]) >= 590
    assert len([p for p in t.published if p[0].startswith("$iothub/twin/GET/")]) == 2


def test_state_save_failure_retried():
    clock = Clock()
    store = StateStore(os.path.join(SCRATCH, "save-retry-state.json"))
    store.data = {"assignedHub": "hub.invalid", "deviceId": "offline-test"}
    t = LoopbackTransport()
    agent = Agent(config(), store, t, clock.now, clock.sleep)
    agent.start()
    real = store.save
    attempts = {"n": 0, "fail": 3}
    def flaky():
        attempts["n"] += 1
        if attempts["fail"] > 0:
            attempts["fail"] -= 1
            raise OSError("atomic_write_failed")
        real()
    store.save = flaky
    try:
        for i in range(40):
            clock.sleep(1)
            agent.step()
        # Three transient failures did not escape; a later save succeeded and reset the counter.
        assert attempts["n"] > 3 and agent.save_failures == 0
        attempts["fail"] = 10 ** 6
        raised = False
        try:
            for i in range(200):
                clock.sleep(1)
                agent.step()
        except OSError:
            raised = True
        assert raised and agent.save_failures >= 12
    finally:
        store.save = real
        agent.stop()


def test_stop_file_requests_clean_stop():
    clock = Clock()
    folder = os.path.join(SCRATCH, "stop-file")
    os.makedirs(folder, exist_ok=True)
    flag = os.path.join(folder, "stop.request")
    with open(flag, "w") as f:
        f.write("stale")  # a stale flag from a previous run must be ignored at startup
    store = StateStore(os.path.join(folder, "state.json"))
    store.data = {"assignedHub": "hub.invalid", "deviceId": "offline-test"}
    cfg = config()
    cfg["configPath"] = os.path.join(folder, "agent-config.json")
    t = LoopbackTransport()
    asked = {"done": False}
    def sleeper(seconds):
        clock.sleep(seconds)
        if clock.now() >= 5 and not asked["done"]:
            asked["done"] = True
            with open(flag, "w") as f:
                f.write("stop")
    agent = Agent(cfg, store, t, clock.now, sleeper)
    assert os.path.exists(flag)
    result = agent.run()
    assert result == 0 and agent.stopping
    assert 5 <= clock.now() < 20, clock.now()
    assert not os.path.exists(flag)


def test_mqtt_callbacks():
    from mqtt_transport import MqttTransport
    site = os.environ.get("PYGATE_PAHO_SITE", "D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp/step02/site-packages")
    sys.path.insert(0, site)
    clock = Clock()
    t = MqttTransport(os.environ["PYGATE_TEST_CERT"], os.environ["PYGATE_TEST_KEY"], clock.now, lambda: 0)
    assert t.context.check_hostname
    assert t.context.verify_mode == 2
    class Reason:
        def __init__(self, failure=False, value=0):
            self.is_failure = failure
            self.value = value
    class Result:
        rc = t.mqtt.MQTTErrorCode.MQTT_ERR_SUCCESS
        mid = 42
    class FakeClient:
        def __init__(self):
            self.open = False
            self.notified = False
            self.first = True
        def tls_set_context(self, context):
            self.context = context
        def username_pw_set(self, username):
            self.username = username
        def connect(self, host, port, keepalive):
            assert port == 8883
            if self.first:
                self.first = False
                raise ConnectionError("synthetic network failure")
        def reconnect(self):
            self.open = True
            self.notified = False
        def loop(self, timeout=0.1):
            if self.open and not self.notified:
                self.notified = True
                self.on_connect(self, None, {}, Reason(), None)
        def publish(self, topic, payload, qos=0):
            return Result()
        def subscribe(self, topic, qos=1):
            return (0, 1)
        def disconnect(self):
            self.open = False
    class FakeMqtt:
        def __init__(self, callback):
            self.CallbackAPIVersion = callback
            self.MQTTv311 = 4
        def Client(self, callback, client_id=None, protocol=None):
            return FakeClient()
    t.mqtt = FakeMqtt(t.mqtt.CallbackAPIVersion)
    reasons = []
    t.on_disconnect = lambda reason: reasons.append(reason)
    t.connect("not-contacted.invalid", "offline-test", "username")
    assert t.next_retry == 1
    t.poll()
    assert not t.connected
    clock.sleep(1)
    t.poll()
    assert t.connected
    t.publish("topic", "payload")
    t.publish("devices/offline-test/messages/events/", "{}", qos=1)
    assert t.metrics["telemetrySent"] == 1 and t.metrics["telemetryPubacks"] == 0
    t._published(t.client, None, 42, Reason(), None)
    assert t.metrics["telemetryPubacks"] == 1 and not t.pending_publications
    t._published(t.client, None, 42, Reason(), None)
    assert t.metrics["telemetryPubacks"] == 1
    t._connected(t.client, None, {}, Reason(True, 135), None)
    assert reasons[-1] == "unauthorized"
    assert not t.connected
    t.disconnect()


def test_reboot_process_exit():
    clock = Clock()
    store = StateStore(os.path.join(SCRATCH, "reboot-state.json"))
    store.data = {"assignedHub": "hub.invalid", "deviceId": "offline-test"}
    transport = LoopbackTransport()
    agent = Agent(config(), store, transport, clock.now, clock.sleep)
    agent.start()
    transport.inject_method("rebootAgent")
    drain(transport)
    agent.step()
    agent.stop()
    assert agent.stopping
    assert agent.runtime.reboot_requested
    print("PASS test_reboot_process_exit")
    sys.exit(75)


def worker_runtime():
    from worker_runtime import PayloadProcess
    clock, transport, twin, ctx, runtime = setup_runtime()
    worker = PayloadProcess(os.path.join(ROOT, "payloads", "thermostat.py"), ctx, SCRATCH, os.path.join(ROOT, "agent", "payload_worker.py"), timeout=8)
    assert worker.call("__validate__")
    runtime.activate_process(worker, worker.path, 1)
    assert runtime.call("on_start")
    return clock, transport, twin, ctx, runtime


def test_worker_windows_contention():
    import time
    from worker_runtime import PayloadProcess
    clock, transport, twin, ctx, runtime = setup_runtime()
    worker = PayloadProcess(os.path.join(ROOT, "payloads", "thermostat.py"), ctx, SCRATCH, os.path.join(ROOT, "agent", "payload_worker.py"), timeout=12)
    try:
        assert worker.call("__validate__")
        response = os.path.join(worker.directory, "response-1.json")
        # Re-read a real worker response while the .NET harness owns the writer.
        with open(os.path.join(SCRATCH, "contention-ready.json"), "w") as f:
            json.dump({"response": response, "directory": worker.directory}, f)
        deadline = time.monotonic() + 10
        while not os.path.exists(os.path.join(SCRATCH, "contention-locked")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert os.path.exists(os.path.join(SCRATCH, "contention-locked"))
        worker.sequence = 0
        assert worker.call("__validate__")
        assert worker.error is None
        assert worker.read_failures >= 2
        assert worker.read_attempts >= 3
        print("CONTENTION_EXECUTED attempts=" + str(worker.read_attempts) + " failures=" + str(worker.read_failures))
    finally:
        worker.close()


def test_worker_death_recovery():
    clock, transport, twin, ctx, runtime = worker_runtime()
    try:
        old = runtime.worker
        old.process.kill()
        old.process.wait(timeout=5)
        before = len([p for p in transport.published if p[0].startswith("devices/")])
        assert runtime.tick()
        assert runtime.worker is not old and runtime.worker.process.poll() is None
        assert runtime.worker_restarts == 1 and runtime.errors == 0
        assert len([p for p in transport.published if p[0].startswith("devices/")]) > before
        assert twin.pending["agent"]["workerReason"] == "worker_exited"
        assert any(e.get("reason") == "worker_exited" for e in ctx.events)
        assert runtime.last_error is None
        clock.sleep(1)
        assert runtime.tick()
        print("WORKER_KILL_EXECUTED oldPid=" + str(old.process.pid) + " newPid=" + str(runtime.worker.process.pid) + " restarts=1 payloadErrors=0")
    finally:
        runtime.stop()


def test_worker_ipc_retention():
    clock, transport, twin, ctx, runtime = worker_runtime()
    try:
        worker = runtime.worker
        for i in range(30):
            clock.sleep(1)
            assert runtime.tick()
        names = os.listdir(worker.directory)
        requests = sorted(n for n in names if n.startswith("request-"))
        responses = sorted(n for n in names if n.startswith("response-"))
        assert worker.sequence >= 30
        assert len(requests) <= worker.ipc_retain and len(responses) <= worker.ipc_retain, (len(requests), len(responses))
        assert "request-" + str(worker.sequence) + ".json" in requests
        assert "response-" + str(worker.sequence) + ".json" in responses
        assert "request-1.json" not in requests and "response-1.json" not in responses
        assert not any(n.endswith(".new") for n in names)
        print("IPC_RETAINED requests=" + str(len(requests)) + " responses=" + str(len(responses)))
    finally:
        runtime.stop()


def test_worker_redacted_output():
    from worker_runtime import PayloadProcess
    clock, transport, twin, ctx, runtime = setup_runtime()
    path = os.path.join(SCRATCH, "printing-payload.py")
    with open(path, "w") as f:
        f.write('import sys\ndef on_tick(ctx):\n    print("SECRET-SENTINEL")\n    sys.stderr.write("SECRET-SENTINEL\\n")\n')
    worker = PayloadProcess(path, ctx, SCRATCH, os.path.join(ROOT, "agent", "payload_worker.py"), timeout=8)
    try:
        assert worker.call("on_tick")
    finally:
        worker.close()
    for channel in ["stdout", "stderr"]:
        with open(os.path.join(worker.directory, "worker." + channel + ".log")) as f:
            text = f.read()
        assert "SECRET-SENTINEL" not in text
        assert "worker_" + channel + "_capture_started" in text


def test_worker_heartbeat_idle():
    import time
    old = os.environ.get("PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC")
    # A two-second minimum is a scheduling stress test, not an idle-liveness
    # budget. Keep idle longer than expiry, with a configurable machine budget.
    heartbeat_budget = max(10, min(30, float(os.environ.get("PYGATE_TEST_HEARTBEAT_SEC", "10"))))
    os.environ["PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC"] = str(heartbeat_budget)
    worker = None
    try:
        clock, transport, twin, ctx, runtime = setup_runtime()
        from worker_runtime import PayloadProcess
        worker = PayloadProcess(os.path.join(ROOT, "payloads", "thermostat.py"), ctx, SCRATCH, os.path.join(ROOT, "agent", "payload_worker.py"), timeout=heartbeat_budget * 2)
        assert worker.call("__validate__"), "heartbeat worker failed validation"
        for i in range(1000):
            worker.touch_heartbeat()
        # Yield regularly to the interpreter's background thread scheduler;
        # no callbacks or foreground heartbeat writes occur during this idle.
        deadline = time.monotonic() + worker.heartbeat_timeout * 2
        observations = []
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                with open(worker.heartbeat_path, encoding="ascii") as f:
                    age = time.time() - float(f.read())
                observations.append({"age": age, "exitCode": worker.process.poll()})
            except (OSError, ValueError):
                observations.append({"readFailure": True, "exitCode": worker.process.poll()})
        with open(os.path.join(SCRATCH, "heartbeat-observations.json"), "w") as f:
            json.dump(observations, f)
        assert worker.process.poll() is None, "worker expired during idle with heartbeat enabled"
        assert worker.call("on_tick"), "worker stopped responding after idle"
    finally:
        if worker:
            worker.close()
        if old is None:
            del os.environ["PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC"]
        else:
            os.environ["PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC"] = old


def test_worker_loopback_soak():
    import time
    from worker_runtime import PayloadProcess
    c = config()
    c["stateFile"] = os.path.join(SCRATCH, "soak-state.json")
    store = StateStore(c["stateFile"])
    store.data = {"assignedHub": "loopback.invalid", "deviceId": os.environ.get("PYGATE_SOAK_AGENT", "soak-agent")}
    transport = LoopbackTransport()
    agent = Agent(c, store, transport)
    agent.start()
    worker = PayloadProcess(os.path.join(ROOT, "payloads", "thermostat.py"), agent.ctx, SCRATCH, os.path.join(ROOT, "agent", "payload_worker.py"), timeout=8)
    assert worker.call("__validate__")
    agent.runtime.activate_process(worker, worker.path, 1)
    assert agent.runtime.call("on_start")
    start = time.monotonic()
    iterations = 0
    disconnect = False
    try:
        while time.monotonic() - start < 600:
            if not disconnect and time.monotonic() - start >= 300:
                transport.force_disconnect()
                disconnect = True
            agent.step()
            iterations += 1
            time.sleep(0.02)
    finally:
        agent.stop()
    elapsed = time.monotonic() - start
    telemetry = len([p for p in transport.published if p[0].startswith("devices/")])
    summary = {"deviceId": agent.ctx.device_id, "elapsedSec": elapsed, "iterations": iterations, "telemetry": telemetry, "payloadErrors": agent.runtime.errors, "workerRestarts": agent.runtime.worker_restarts, "events": list(agent.ctx.events), "disconnectExecuted": disconnect, "lastError": agent.runtime.last_error, "transport": "local-loopback", "azureContacted": False}
    with open(os.path.join(SCRATCH, "soak-summary.json"), "w") as f:
        json.dump(summary, f)
    print("SOAK_SUMMARY " + json.dumps(summary))
    assert elapsed >= 600
    assert telemetry >= 550
    assert agent.runtime.errors == 0 and agent.runtime.worker_restarts == 0
    assert agent.runtime.last_error is None


def test_worker_parent_lifetime():
    import subprocess
    import time
    script = os.path.join(SCRATCH, "parent-lifetime.py")
    ready = os.path.join(SCRATCH, "parent-worker.pid")
    if os.path.exists(ready):
        os.replace(ready, ready + ".previous")
    source = "import os, sys, time\nos.environ['PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC'] = '2'\nsys.argv[0] = " + repr(os.path.join(os.path.dirname(ROOT), "tests", "python", "run_tests.py")) + "\nsys.path.insert(0, " + repr(os.path.join(ROOT, "tests")) + ")\nimport run_tests as tests\nfrom main import Agent\nfrom config import StateStore\nfrom mqtt_transport import LoopbackTransport\nstore = StateStore(" + repr(os.path.join(SCRATCH, "parent-state.json")) + ")\nstore.data = {'assignedHub':'hub.invalid','deviceId':'offline-test'}\nagent = Agent(tests.config(), store, LoopbackTransport())\nagent.start()\nagent.start()\nwith open(" + repr(ready) + ", 'w') as f: f.write(str(agent.runtime.worker.process.pid))\nwhile True: time.sleep(1)\n"
    with open(script, "w") as f:
        f.write(source)
    parent = subprocess.Popen(["pysharp", "run", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not os.path.exists(ready) and time.monotonic() < deadline:
            assert parent.poll() is None
            time.sleep(0.1)
        assert os.path.exists(ready)
        with open(ready) as f:
            pid = int(f.read())
        parent.kill()
        parent.wait(timeout=5)
        check = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "$end=(Get-Date).AddSeconds(15); while ((Get-Process -Id " + str(pid) + " -ErrorAction SilentlyContinue) -and (Get-Date) -lt $end) { Start-Sleep -Milliseconds 250 }; if (Get-Process -Id " + str(pid) + " -ErrorAction SilentlyContinue) { exit 1 }"], timeout=25)
        assert check.returncode == 0
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)


def test_agent_stop_no_workers():
    import worker_runtime as worker_module
    store = StateStore(os.path.join(SCRATCH, "cleanup-state.json"))
    store.data = {"assignedHub": "hub.invalid", "deviceId": "offline-test"}
    agent = Agent(config(), store, LoopbackTransport())
    workers = []
    try:
        agent.start()
        agent.start()
        workers = [agent.runtime.worker]
        assert agent.runtime.worker is not None
    finally:
        agent.stop()
    for worker in workers:
        print("CLEANUP_PID " + str(worker.process.pid) + " exit=" + str(worker.process.poll()))
        assert worker.process.poll() is not None
    assert not worker_module.ACTIVE_WORKERS


TESTS = [test_agent_stop_no_workers, test_worker_parent_lifetime, test_worker_windows_contention, test_worker_death_recovery, test_worker_ipc_retention, test_worker_redacted_output, test_worker_heartbeat_idle, test_dps_happy_retry_cache, test_dps_throttle_timeout_permission, test_desired_convergence_stale_validation, test_alarm_hysteresis, test_report_throttle_coalesce_temperature, test_payload_exception_containment, test_methods, test_reconnect_backoff_reprovision, test_state_encoder_and_loader, test_state_save_failure_retried, test_stop_file_requests_clean_stop, test_ten_minutes_virtual_disconnect]
from ota_cases import OTA_TESTS
TESTS.extend(OTA_TESTS)
from approved_ota_cases import APPROVED_OTA_TESTS
TESTS.extend(APPROVED_OTA_TESTS)
from adr_cases import ADR_TESTS
TESTS.extend(ADR_TESTS)
from log_cases import LOG_TESTS
TESTS.extend(LOG_TESTS)
if len(sys.argv) > 1 and sys.argv[1] == "test_worker_loopback_soak":
    TESTS.append(test_worker_loopback_soak)
if len(sys.argv) > 1 and sys.argv[1] == "test_mqtt_callbacks":
    TESTS.append(test_mqtt_callbacks)
if len(sys.argv) > 1 and sys.argv[1] == "test_reboot_process_exit":
    test_reboot_process_exit()
if __name__ == "__main__":
    selected = [test for test in TESTS if len(sys.argv) == 1 or test.__name__ == sys.argv[1]]
    assert selected
    for test in selected:
        import worker_runtime as worker_module
        from worker_runtime import close_all_workers
        workers = []
        try:
            test()
        finally:
            workers = list(worker_module.ACTIVE_WORKERS)
            close_all_workers()
            assert all(worker.process.poll() is not None for worker in workers)
        print("PASS " + test.__name__)
    print("ALL_DEVICE_TESTS_PASSED " + str(len(selected)))
