# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import math
import time
from mqtt_transport import merge, query
from log import logger_for, emit


class Twin:
    def __init__(self, transport, on_desired=None, clock=time.monotonic):
        self.transport = transport
        self.logger = logger_for(transport)
        self.last_desired_seen = {}
        self.on_desired = on_desired
        self.clock = clock
        self.desired = {"targetTemperature": 21.0, "telemetryIntervalSec": 1, "thresholds": {"high": 26.0, "low": 16.0, "hysteresis": 0.5}}
        self.reported = {}
        self.pending = {}
        self.inflight = {}
        self.version = -1
        self.rid = 0
        self.last_report = -1000000
        self.last_temperature = -1000000
        self.get_rid = None

    def connected(self):
        # Preserve unacknowledged reports across reconnection.
        if self.inflight:
            for rid, entry in list(self.inflight.items()):
                retry = dict(entry[0])
                merge(retry, self.pending)
                self.pending = retry
            self.inflight = {}
        self.transport.subscribe("$iothub/twin/res/#")
        self.transport.subscribe("$iothub/twin/PATCH/properties/desired/#")
        self.rid += 1
        self.get_rid = str(self.rid)
        self.get_sent = self.clock()
        self.transport.publish("$iothub/twin/GET/?$rid=" + self.get_rid, "", qos=1)

    def apply(self, patch, version=None):
        version = patch.get("$version", version)
        if version is None or int(version) <= self.version:
            return False
        clean = {k: v for k, v in patch.items() if not k.startswith("$")}
        # Validate operational desired values before committing any patch.
        if "telemetryIntervalSec" in clean:
            interval = float(clean["telemetryIntervalSec"])
            if not math.isfinite(interval):
                raise ValueError("Invalid interval")
            clean["telemetryIntervalSec"] = max(1, min(3600, interval))
        if "targetTemperature" in clean:
            target = float(clean["targetTemperature"])
            if not math.isfinite(target):
                raise ValueError("Invalid target")
            clean["targetTemperature"] = target
        if "thresholds" in clean and clean["thresholds"] is not None:
            t = dict(self.desired.get("thresholds", {"high": 26, "low": 16, "hysteresis": 0.5}))
            merge(t, clean["thresholds"])
            for key in ["high", "low", "hysteresis"]:
                t[key] = float(t[key])
                if not math.isfinite(t[key]):
                    raise ValueError("Invalid threshold")
            if t["high"] <= t["low"] or t["hysteresis"] < 0:
                raise ValueError("Invalid thresholds")
            clean["thresholds"] = t
        merge(self.desired, clean)
        self.version = int(version)
        if self.on_desired:
            self.on_desired(clean)
        return True

    def log_desired(self, patch, topic, version=None):
        # Compare the received document, not the normalized operational values.
        # This diagnostic snapshot never supplies payload state or OTA input.
        try:
            clean = {k: v for k, v in patch.items() if not k.startswith("$")}
            seen = json.loads(json.dumps(self.last_desired_seen))
            merge(seen, clean)
            changed = sorted(k for k in set(seen) | set(self.last_desired_seen) if (k in seen) != (k in self.last_desired_seen) or seen.get(k) != self.last_desired_seen.get(k))
            self.last_desired_seen = seen
            fields = {"topic": topic, "$version": patch.get("$version", version), "changed": changed}
            emit(self, "debug", "twin", "IN desired", **fields)
            emit(self, "trace", "twin", "IN desired", body=patch, **fields)
        except Exception:
            pass

    def message(self, topic, payload):
        if topic.startswith("$iothub/twin/PATCH/properties/desired/"):
            body = json.loads(payload)
            self.log_desired(body, topic, query(topic).get("$version"))
            applied = self.apply(body, query(topic).get("$version"))
            emit(self, "debug", "twin", "desired_result", applied=applied, version=self.version)
            return applied
        if topic.startswith("$iothub/twin/res/"):
            rid = query(topic).get("$rid")
            status = int(topic.split("/")[3])
            sent = self.inflight[rid][1] if rid in self.inflight else getattr(self, "get_sent", None) if rid == self.get_rid else None
            fields = {"rid": rid, "status": status, "latencyMs": round((self.clock() - sent) * 1000, 3) if sent is not None else None}
            emit(self, "debug", "twin", "ACK", **fields)
            emit(self, "trace", "twin", "ACK", **fields)
            if rid == self.get_rid and status == 200:
                body = json.loads(payload)
                self.log_desired(body.get("desired", {}), topic)
                self.apply(body.get("desired", {}))
            elif rid in self.inflight:
                patch, sent = self.inflight.pop(rid)
                if status in [200, 204]:
                    merge(self.reported, patch)
                else:
                    # Newer pending values win over rejected values.
                    retry = dict(patch)
                    merge(retry, self.pending)
                    self.pending = retry
                    emit(self, "debug", "twin", "report_retry", rid=rid, reason="rejected", status=status)
            return True
        return False

    def report(self, patch):
        # Coalesce only actual changes; avoid logging arbitrary payload content.
        for key, value in patch.items():
            if value != self.reported.get(key) or key in self.pending:
                if isinstance(value, dict) and isinstance(self.pending.get(key), dict):
                    merge(self.pending[key], value)
                else:
                    self.pending[key] = value

    def flush(self):
        now = self.clock()
        for rid, entry in list(self.inflight.items()):
            if now - entry[1] >= 30:
                emit(self, "debug", "twin", "report_retry", rid=rid, reason="ack_timeout", latencyMs=round((now - entry[1]) * 1000, 3))
                retry = dict(entry[0])
                merge(retry, self.pending)
                self.pending = retry
                del self.inflight[rid]
        if not self.transport.connected or self.inflight or now - self.last_report < 5:
            return
        patch = dict(self.pending)
        if now - self.last_temperature < 30:
            patch.pop("currentTemperature", None)
        if not patch:
            return
        self.rid += 1
        rid = str(self.rid)
        self.transport.publish("$iothub/twin/PATCH/properties/reported/?$rid=" + rid, json.dumps(patch), qos=1)
        self.inflight[rid] = (patch, now)
        for key in patch:
            self.pending.pop(key, None)
        self.last_report = now
        if "currentTemperature" in patch:
            self.last_temperature = now
