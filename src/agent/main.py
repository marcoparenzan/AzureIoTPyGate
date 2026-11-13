# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import os
import signal
import sys
import time
from config import load_config, StateStore
from dps import Provisioner
from mqtt_transport import MqttTransport, LoopbackTransport, backoff
from twin import Twin
from runtime import Context, Runtime
from methods import Methods
from ota import OtaController
from log import Logger, emit


MAX_SAVE_FAILURES = 12  # about one minute of 5 s retries
STOP_FILE = "stop.request"


class Agent:
    def __init__(self, config, store, transport, clock=time.monotonic, sleep=time.sleep):
        self.config = config
        self.store = store
        self.transport = transport
        self.logger = Logger(config)
        transport.logger = self.logger
        store.logger = self.logger
        self.clock = clock
        self.sleep = sleep
        self.twin = Twin(transport, clock=clock)
        self.ctx = Context(store.data.get("deviceId", config["registrationId"]), transport, self.twin, store.data)
        self.ctx.state["injectFault"] = bool(config.get("injectFault", False))
        self.runtime = Runtime(self.ctx, os.path.join(config["payloadDir"], "thermostat.py"), clock)
        self.twin.on_desired = self.runtime.desired_changed
        self.ota = OtaController(config, store, self.runtime)
        self.methods = Methods(transport, self.runtime)
        self.stopping = False
        # A control file next to agent-config.json asks for a clean stop (Ctrl+C is not
        # reliable across launchers). Tests without a config path have it disabled.
        config_path = config.get("configPath")
        self.stop_flag = os.path.join(os.path.dirname(config_path), STOP_FILE) if config_path else None
        self.next_stop_check = 0
        self.reprovision = False
        self.next_save = clock()
        self.reconnect_at = clock()
        self.next_provision = clock()
        self.provision_failures = 0
        transport.on_message = self.message
        transport.on_connect = self.connected
        transport.on_disconnect = self.disconnected

    def connected(self):
        self.ctx.log("connected")
        self.twin.connected()
        self.methods.connected()
        self.ctx.report({"agent": {"version": "1.0.0", "startedAt": self.ctx.now(), "pysharp": "2.5.1-preview.8-required"}})
        self.ctx.report({"alarmState": self.ctx.state.get("alarmState", "normal")})

    def disconnected(self, reason):
        self.ctx.log("disconnected")
        emit(self, "info", "agent", "disconnected", reason=reason)
        emit(self, "debug", "agent", "reconnect_scheduled", delaySec=1, reason=reason)
        self.reconnect_at = self.clock() + 1
        if reason in ["unauthorized", "not-found"]:
            self.reprovision = True

    def message(self, topic, payload):
        callback_started = self.clock()
        try:
            if not self.methods.message(topic, payload):
                self.twin.message(topic, payload)
        except Exception:
            self.ctx.log("message_error")
        finally:
            emit(self, "debug", "agent", "message_callback", topic=topic, latencyMs=round((self.clock() - callback_started) * 1000, 3))

    def start(self):
        emit(self, "info", "agent", "startup", deviceId=self.ctx.device_id, configPath=self.config.get("configPath", "<programmatic>"))
        dps = Provisioner(self.config, self.store.data, self.transport, self.clock, self.sleep)
        host, device_id = dps.register()
        self.ctx.device_id = device_id
        self.store.save()
        if self.runtime.namespace is not None or self.runtime.worker is not None:
            self.runtime.stop()
        # Bootstrap owns verification and activation, including the initial
        # trusted local seed. Failure/absence means connected without payload.
        self.ota.bootstrap()
        self.transport.connect(host, device_id, host + "/" + device_id + "/?api-version=2021-04-12")

    def step(self):
        if self.config.get("identityFamily") == "X509DpsAdr" and not self.reprovision:
            if Provisioner(self.config, self.store.data, self.transport).renewal_due():
                self.reprovision = True
        if self.reprovision:
            if not self.config.get("allowReprovision", True):
                raise ConnectionError("Reprovisioning prohibited for this single-registration run")
            if self.clock() < self.next_provision:
                return
            self.transport.disconnect()
            provisioner = Provisioner(self.config, self.store.data, self.transport, self.clock, self.sleep)
            provisioner.clear_assignment()
            self.store.save()
            try:
                self.start()
                self.reprovision = False
                self.provision_failures = 0
            except Exception:
                self.ctx.log("disconnected")
                self.next_provision = self.clock() + backoff(self.provision_failures)
                self.provision_failures += 1
                emit(self, "debug", "agent", "provision_retry", attempt=self.provision_failures, delaySec=self.next_provision - self.clock())
                return
        if isinstance(self.transport, LoopbackTransport) and not self.transport.connected and self.clock() >= self.reconnect_at:
            host = self.store.data["assignedHub"]
            self.transport.connect(host, self.ctx.device_id, host + "/" + self.ctx.device_id + "/?api-version=2021-04-12")
        self.transport.poll(0.1)
        if self.transport.connected:
            self.ota.poll()
            self.ota.tick_result(self.runtime.tick())
            self.twin.flush()
        if self.clock() >= self.next_save:
            try:
                self.store.save()
                self.save_failures = 0
            except OSError:
                # A transient lock on the state file must not stop the device (the
                # launchers restart only on exit 75). Retry on the next interval and
                # fail loudly only if it persists.
                self.save_failures = getattr(self, "save_failures", 0) + 1
                emit(self, "info", "state", "save_failed", consecutive=self.save_failures)
                if self.save_failures >= MAX_SAVE_FAILURES:
                    raise
            self.next_save = self.clock() + 5
        if self.runtime.reboot_requested:
            # Give the QoS1 method reply time to leave the client before exit.
            until = self.clock() + 1
            while self.clock() < until:
                self.transport.poll(0.05)
                self.sleep(0.01)
            self.stopping = True

    def stop(self):
        emit(self, "info", "agent", "shutdown", reason="reboot" if self.runtime.reboot_requested else "stop_requested")
        try:
            try:
                self.runtime.stop()
                self.store.save()
            finally:
                try:
                    if hasattr(self.transport, "drain"):
                        drained = self.transport.drain()
                        emit(self, "debug", "mqtt", "drain_result", completed=drained)
                finally:
                    self.transport.disconnect()
        finally:
            # All producers stop before the bounded logger flush; failures in
            # runtime, persistence or transport must not leak the log thread.
            emit(self, "info", "agent", "stopped")
            try:
                self.logger.close()
            except Exception:
                pass

    def clear_stop_flag(self):
        # The flag is transient control state; a stale one must not stop the next run.
        try:
            if self.stop_flag and os.path.exists(self.stop_flag):
                os.remove(self.stop_flag)
        except OSError:
            pass

    def stop_requested(self):
        if not self.stop_flag or self.clock() < self.next_stop_check:
            return False
        self.next_stop_check = self.clock() + 1
        try:
            return os.path.exists(self.stop_flag)
        except OSError:
            return False

    def run(self, duration=None, forced_disconnect=False):
        forced = False
        self.clear_stop_flag()
        try:
            self.start()
            started = self.clock()
            while not self.stopping:
                if self.stop_requested():
                    emit(self, "info", "agent", "stop_file_seen")
                    self.stopping = True
                    break
                if duration is not None and self.clock() - started >= duration:
                    break
                if forced_disconnect and not forced and self.clock() - started >= min(5, duration / 2):
                    self.transport.force_disconnect()
                    forced = True
                self.step()
                self.sleep(0.02)
        finally:
            self.stop()
            self.clear_stop_flag()
        return 75 if self.runtime.reboot_requested else 0


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ["--loopback", "--live"]:
        print("Usage: pysharp run main.py --loopback|--live external-config.json [duration-seconds]")
        return 2
    config = load_config(sys.argv[2])
    if config.get("pahoSite"):
        sys.path.insert(0, config["pahoSite"])
    store = StateStore(config["stateFile"])
    offline = sys.argv[1] == "--loopback"
    if offline:
        store.data["assignedHub"] = "loopback.invalid"
        store.data["deviceId"] = config["registrationId"]
        transport = LoopbackTransport({"$version": 1, "targetTemperature": 21, "telemetryIntervalSec": 1, "thresholds": {"high": 26, "low": 16, "hysteresis": 0.5}})
    else:
        transport = MqttTransport(config["cert"], config["key"])
    agent = Agent(config, store, transport)
    def shutdown(signum, frame):
        agent.stopping = True
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else None
    result = agent.run(duration, offline and duration is not None)
    if offline:
        events = len([entry for entry in transport.published if entry[0].startswith("devices/")])
        print("LOOPBACK_COMPLETED telemetry=" + str(events) + " payloadErrors=" + str(agent.runtime.errors))
    else:
        import json
        summary = dict(transport.metrics)
        summary["payloadErrors"] = agent.runtime.errors
        summary["messageErrors"] = len([entry for entry in agent.ctx.events if entry["event"] == "message_error"])
        summary["pendingPublications"] = len(transport.pending_publications)
        print("LIVE_COMPLETED " + json.dumps(summary))
    return result


if __name__ == "__main__":
    sys.exit(main())
