# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from log import logger_for, emit


class JsonEncoder:
    def encode(self, body, props):
        properties = dict(props or {})
        properties["$.ct"] = "application/json"
        properties["$.ce"] = "utf-8"
        properties["encoding"] = "json"
        return json.dumps(body), urllib.parse.urlencode(properties)


class Context:
    def __init__(self, device_id, transport, twin, state, encoder=None):
        self.device_id = device_id
        self.transport = transport
        self.twin = twin
        self.state = state
        self.encoder = encoder or JsonEncoder()
        self.logger = logger_for(transport)
        self.events = []
        self.payload_version = 1

    @property
    def desired(self):
        # A deep copy prevents payload mutations from altering the twin.
        return json.loads(json.dumps(self.twin.desired))

    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def report(self, patch):
        self.twin.report(patch)

    def log(self, message, reason=None):
        # Log fixed event identifiers, never user content, paths or exceptions.
        allowed = ["payload_started", "payload_stopped", "payload_error", "message_error", "disconnected", "connected", "worker_restarting", "worker_recovered", "worker_recovery_failed"]
        event = message if message in allowed else "payload_event"
        entry = {"ts": self.now(), "event": event}
        reasons = ["worker_exited", "watchdog_timeout", "response_read_failure", "payload_exception", "payload_watchdog_timeout"]
        if reason in reasons:
            entry["reason"] = reason
        self.events.append(entry)
        self.events = self.events[-50:]
        emit(self, "info", "runtime", event, **({"reason": reason} if reason in reasons else {}))

    def send_telemetry(self, body, props=None):
        payload, properties = self.encoder.encode(body, props)
        if len(payload.encode("utf-8")) > 240000:
            raise ValueError("Telemetry exceeds safe limit")
        self.transport.publish("devices/" + self.device_id + "/messages/events/" + properties, payload, qos=1)


class Runtime:
    def __init__(self, ctx, payload_path, clock=time.monotonic):
        self.ctx = ctx
        self.clock = clock
        self.namespace = None
        self.payload_path = payload_path
        self.errors = 0
        self.next_tick = clock()
        self.started = clock()
        self.reboot_requested = False
        self.stopped = False
        self.worker = None
        self.last_error = None
        self.worker_restarts = 0
        self.ota = None

    def activate_process(self, worker, path, version):
        self.call("on_stop")
        if self.worker:
            self.worker.close()
        self.worker = worker
        self.namespace = {}
        self.payload_path = path
        self.ctx.payload_version = version
        self.next_tick = self.clock()
        self.stopped = False

    def load(self):
        with open(self.payload_path, encoding="utf-8-sig") as f:
            source = f.read()
        namespace = {"__name__": "forge_payload", "__file__": self.payload_path}
        exec(compile(source, self.payload_path, "exec"), namespace)
        for entry in ["on_start", "on_tick", "on_desired", "on_stop"]:
            if not callable(namespace.get(entry)):
                raise ValueError("Payload entry point missing: " + entry)
        self.namespace = namespace
        self.stopped = False
        success = self.call("on_start")
        self.ctx.report({"payload": {"name": "thermostat", "version": self.ctx.payload_version, "status": "running" if success else "failed", "lastGood": 1}})
        return success

    def call(self, name, patch=None):
        callback_started = self.clock()
        try:
            if self.worker:
                result = self.worker.call(name, patch)
                self.last_error = self.worker.error
                if not result and name != "on_stop" and self.last_error in ["worker_exited", "watchdog_timeout", "response_read_failure"]:
                    reason = self.last_error
                    self.worker_restarts += 1
                    self.ctx.log("worker_restarting", reason)
                    self.ctx.report({"agent": {"workerRestarts": self.worker_restarts, "workerReason": reason, "workerStatus": "restarting"}})
                    self.worker = self.worker.replacement()
                    # Committed ctx state survives, but module globals reset.
                    # Initialize the replacement directly (no recursive recovery)
                    # before one retry of the failed callback. Replay the full
                    # current desired state, without resubmitting desired OTA.
                    result = self.worker.call("on_start")
                    self.last_error = self.worker.error
                    if result:
                        result = self.worker.call("on_desired", self.ctx.desired)
                        self.last_error = self.worker.error
                    initialized = result
                    if initialized and name != "on_start":
                        result = self.worker.call(name, patch)
                        self.last_error = self.worker.error
                    # An on_start retry was already completed by initialization;
                    # never run it twice. Initialization failures suppress retry.
                    # Two tick deadlines identify a hang only if initialization
                    # succeeded; exit/read failures remain infrastructure errors.
                    if initialized and not result and name == "on_tick" and reason == "watchdog_timeout" and self.last_error == "watchdog_timeout":
                        self.last_error = "payload_watchdog_timeout"
                    self.ctx.log("worker_recovered" if result else "worker_recovery_failed", reason if result else self.last_error)
                    self.ctx.report({"agent": {"workerRestarts": self.worker_restarts, "workerReason": reason if result else self.last_error, "workerStatus": "recovered" if result else "failed"}})
                if not result and self.last_error in ["payload_exception", "payload_watchdog_timeout"]:
                    self.errors += 1
                    self.ctx.log("payload_error", self.last_error)
                    self.ctx.report({"agent": {"payloadErrors": self.errors, "payloadErrorReason": self.last_error}})
                return result
            if self.namespace is None:
                return
            if patch is None:
                self.namespace[name](self.ctx)
            else:
                self.namespace[name](self.ctx, patch)
            return True
        except Exception:
            if self.worker:
                self.last_error = "response_read_failure"
                self.ctx.log("worker_recovery_failed", self.last_error)
                self.ctx.report({"agent": {"workerReason": self.last_error, "workerStatus": "failed"}})
            else:
                self.last_error = "payload_exception"
                self.errors += 1
                self.ctx.log("payload_error", self.last_error)
                self.ctx.report({"agent": {"payloadErrors": self.errors, "payloadErrorReason": self.last_error}})
            return False
        finally:
            emit(self.ctx, "debug", "runtime", "callback", callback=name, latencyMs=round((self.clock() - callback_started) * 1000, 3), status=self.last_error or "complete")

    def desired_changed(self, patch):
        self.call("on_desired", patch)
        desired = self.ctx.desired
        applied = {k: desired[k] for k in ["targetTemperature", "thresholds", "telemetryIntervalSec"] if k in desired}
        self.ctx.report({"applied": applied})
        if self.ota and "payload" in patch and isinstance(patch["payload"], dict):
            self.ota.submit(patch["payload"])

    def tick(self):
        if self.stopped or self.clock() < self.next_tick:
            return
        interval = max(1, min(3600, float(self.ctx.desired.get("telemetryIntervalSec", 1))))
        self.next_tick = self.clock() + interval
        return self.call("on_tick")

    def restart(self):
        if self.ota:
            try:
                self.ota.restore(self.ota.store.data["current"])
                return True
            except Exception:
                self.ota.safe_state("restore_failed")
                return False
        self.call("on_stop")
        success = self.load()
        self.next_tick = self.clock()
        return success

    def stop(self):
        if not self.stopped:
            try:
                self.call("on_stop")
            finally:
                worker = self.worker
                self.worker = None
                self.namespace = None
                self.stopped = True
                if worker:
                    worker.close()

    def diagnostics(self):
        return {"agentVersion": "1.0.0", "payloadVersion": self.ctx.payload_version, "uptimeSec": int(self.clock() - self.started), "payloadErrors": self.errors, "events": list(self.ctx.events)}
