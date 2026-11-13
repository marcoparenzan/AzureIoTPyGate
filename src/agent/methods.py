# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import urllib.parse
import time
from log import emit
from mqtt_transport import query


class Methods:
    def __init__(self, transport, runtime):
        self.transport = transport
        self.runtime = runtime

    def connected(self):
        self.transport.subscribe("$iothub/methods/POST/#")

    def message(self, topic, payload):
        if not topic.startswith("$iothub/methods/POST/"):
            return False
        started = time.monotonic()
        rid = query(topic).get("$rid")
        if not rid:
            emit(self.transport, "debug", "methods", "request_ignored", reason="missing_rid")
            return True
        name = urllib.parse.unquote(topic.split("/")[3])
        reboot = False
        try:
            body = json.loads(payload) if payload else {}
            if not isinstance(body, dict):
                raise ValueError("Expected object")
            if name == "ping":
                status, result = 200, {"ts": self.runtime.ctx.now()}
            elif name == "getDiagnostics":
                status, result = 200, self.runtime.diagnostics()
            elif name == "restartPayload":
                success = self.runtime.restart()
                status, result = (200, {"restarted": True}) if success else (409, {"error": "Payload start failed"})
            elif name == "rebootAgent":
                status, result = 200, {"rebooting": True}
                reboot = True
            elif name == "applyPayload" and self.runtime.ota:
                status, result = self.runtime.ota.apply_sync(body, self.runtime.ota.config.get("otaMethodTimeoutSec", 60))
            elif name == "rollbackPayload" and self.runtime.ota:
                success = self.runtime.ota.rollback()
                status, result = (200 if success else 409), {"status": self.runtime.ota.status, "error": self.runtime.ota.error}
            elif name in ["applyPayload", "rollbackPayload"]:
                status, result = 501, {"error": "OTA controller not attached"}
            else:
                status, result = 404, {"error": "Unknown method"}
        except ValueError:
            status, result = 400, {"error": "Invalid method request"}
        except Exception:
            status, result = 500, {"error": "Method failed"}
        emit(self.transport, "debug", "methods", "callback_result", name=name, rid=rid, status=status, latencyMs=round((time.monotonic() - started) * 1000, 3))
        emit(self.transport, "trace", "methods", "OUT response", name=name, rid=rid, status=status, body=result)
        self.transport.publish("$iothub/methods/res/" + str(status) + "/?$rid=" + urllib.parse.quote(rid, safe=""), json.dumps(result), qos=1)
        if reboot:
            self.runtime.reboot_requested = True
        return True
