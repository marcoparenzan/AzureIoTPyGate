# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# Entry script: derive paths from argv, not __file__ (PySharp CLI gap).
import json
import os
import sys
import time
from datetime import datetime, timezone


class WorkerContext:
    def __init__(self, request):
        self.state = request["state"]
        self._desired = request["desired"]
        self.device_id = request["deviceId"]
        self.payload_version = request["version"]
        self.effects = []
    @property
    def desired(self):
        return json.loads(json.dumps(self._desired))
    def now(self):
        return datetime.now(timezone.utc).isoformat()
    def send_telemetry(self, body, props=None):
        self.effects.append(["telemetry", body, props])
    def report(self, patch):
        self.effects.append(["report", patch])
    def log(self, event):
        self.effects.append(["log", event])


class RedactedOutput:
    # Trusted payloads may print secrets by accident. Preserve evidence that
    # output occurred, not the user-controlled contents or exception messages.
    def __init__(self, stream, channel):
        self.stream = stream
        self.channel = channel
    def write(self, text):
        self.stream.write("worker_" + self.channel + "_redacted chars=" + str(len(text)) + "\n")
        self.stream.flush()
        return len(text)
    def flush(self):
        self.stream.flush()


def read_response(path):
    try:
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        print(json.dumps(result))
    except (OSError, ValueError):
        sys.exit(2)


def run():
    path, directory, heartbeat_path = sys.argv[1], sys.argv[2], sys.argv[3]
    heartbeat_timeout = float(sys.argv[4])
    last_heartbeat = time.time()
    sys.stdout = RedactedOutput(sys.stdout, "stdout")
    sys.stderr = RedactedOutput(sys.stderr, "stderr")
    sys.path.insert(0, os.path.dirname(path))
    namespace = {"__name__": "forge_payload", "__file__": path}
    loaded = False
    previous = 0
    while True:
        try:
            with open(heartbeat_path, encoding="ascii") as f:
                last_heartbeat = max(last_heartbeat, float(f.read()))
        except (OSError, ValueError):
            pass
        if time.time() - last_heartbeat > heartbeat_timeout:
            sys.exit(0)
        request_path = os.path.join(directory, "request-" + str(previous + 1) + ".json")
        if not os.path.exists(request_path):
            time.sleep(0.01)
            continue
        with open(request_path, encoding="utf-8") as f:
            request = json.load(f)
        if request["id"] == previous:
            time.sleep(0.01)
            continue
        previous = request["id"]
        ctx = WorkerContext(request)
        result = {"id": previous, "ok": False, "error": "payload_exception"}
        try:
            if not loaded:
                with open(path, encoding="utf-8-sig") as f:
                    exec(compile(f.read(), path, "exec"), namespace)
                loaded = True
            if request["callback"] == "__validate__":
                if not callable(namespace.get("on_tick")):
                    raise ValueError("Missing on_tick")
                for name in ["on_start", "on_desired", "on_stop"]:
                    if name in namespace and not callable(namespace[name]):
                        raise ValueError("Invalid optional entry point")
            callback = namespace.get(request["callback"])
            if callback:
                if request["callback"] == "on_desired":
                    callback(ctx, request.get("patch", {}))
                else:
                    callback(ctx)
            elif request["callback"] == "on_tick":
                raise ValueError("Missing on_tick")
            result = {"id": previous, "ok": True, "state": ctx.state, "effects": ctx.effects}
        except BaseException:
            pass
        target = os.path.join(directory, "response-" + str(previous) + ".json")
        with open(target + ".new", "w", encoding="utf-8") as f:
            json.dump(result, f)
        os.replace(target + ".new", target)


if __name__ == "__main__":
    if sys.argv[1] == "--read-response":
        read_response(sys.argv[2])
    else:
        run()
