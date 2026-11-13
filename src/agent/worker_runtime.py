# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
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
import subprocess
import tempfile
import time
import threading


ACTIVE_WORKERS = []


def close_all_workers():
    for worker in list(ACTIVE_WORKERS):
        worker.close()


INFRASTRUCTURE_ERRORS = ["worker_exited", "watchdog_timeout", "response_read_failure"]


class PayloadProcess:
    def __init__(self, path, ctx, directory, worker_path, timeout=5, command="pysharp", heartbeat_timeout=30, ipc_retain=5):
        os.makedirs(directory, exist_ok=True)
        self.root = directory
        self.path = path
        self.worker_path = worker_path
        self.command = command
        self.directory = tempfile.mkdtemp(prefix="payload-worker-", dir=directory)
        self.ctx = ctx
        self.timeout = max(0.2, min(60, float(timeout)))
        configured_heartbeat = os.environ.get("PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC", heartbeat_timeout)
        self.heartbeat_timeout = max(2, min(300, float(configured_heartbeat)))
        self.heartbeat_interval = min(1, self.heartbeat_timeout / 4)
        self.heartbeat_path = os.path.join(self.directory, "agent-heartbeat.txt")
        self.heartbeat_stopped = False
        self.heartbeat_lock = threading.Lock()
        self.sequence = 0
        # Consumed request/response files hold desired properties and state. Keep only
        # the newest few for diagnosis so storage and exposure stay bounded.
        self.ipc_retain = max(0, min(1000, int(ipc_retain)))
        self.ipc_pruned = 0
        self.error = None
        self.read_attempts = 0
        self.read_failures = 0
        # Create fixed-name evidence before the first read so an external
        # contention harness can observe the first retry without a file race.
        with open(os.path.join(self.directory, "reader.stderr.log"), "a", encoding="utf-8"):
            pass
        # preview.8 supports PIPE but ignores file-object redirection. Drain both
        # streams independently; only fixed identifiers/counts reach evidence.
        self.capture_done = {"stdout": False, "stderr": False}
        self.touch_heartbeat()
        self.process = subprocess.Popen([command, "run", worker_path, path, self.directory, self.heartbeat_path, str(self.heartbeat_timeout)], cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ACTIVE_WORKERS.append(self)
        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        for channel in ["stdout", "stderr"]:
            stream = self.process.stdout if channel == "stdout" else self.process.stderr
            thread = threading.Thread(target=self.capture, args=(stream, channel), daemon=True)
            thread.start()

    def capture(self, stream, channel):
        with open(os.path.join(self.directory, "worker." + channel + ".log"), "a", encoding="utf-8") as evidence:
            evidence.write("worker_" + channel + "_capture_started\n")
            evidence.flush()
            while True:
                text = stream.readline()
                if not text:
                    break
                evidence.write("worker_" + channel + "_redacted chars=" + str(len(text)) + "\n")
                evidence.flush()
        self.capture_done[channel] = True

    def touch_heartbeat(self):
        try:
            with self.heartbeat_lock:
                # Direct write avoids PySharp os.replace leaking a Windows
                # UnauthorizedAccessException. The worker retains its last
                # valid timestamp while a truncate/write is briefly partial.
                with open(self.heartbeat_path, "w", encoding="ascii") as f:
                    f.write(str(time.time()))
        except OSError:
            # A transient writer failure is retried by the background loop. A
            # persistent failure deliberately lets the worker time out safely.
            pass

    def heartbeat_loop(self):
        while not self.heartbeat_stopped:
            self.touch_heartbeat()
            time.sleep(self.heartbeat_interval)

    def replacement(self):
        self.close()
        return PayloadProcess(self.path, self.ctx, self.root, self.worker_path, self.timeout, self.command, self.heartbeat_timeout)

    def read_response(self, path, remaining):
        # PySharp preview.10 translates Windows sharing violations to OSError.
        # Read in this process and let the callback loop retry incomplete or
        # temporarily locked files. This avoids launching one PySharp process
        # per callback, which caused severe process churn in concurrent soaks.
        try:
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
            if not isinstance(result, dict) or result.get("id") != self.sequence or not isinstance(result.get("ok"), bool):
                return None
            if result["ok"] and (not isinstance(result.get("state"), dict) or not isinstance(result.get("effects"), list)):
                return None
            return result
        except (ValueError, OSError):
            # Fixed evidence only: never persist exception text or payload data.
            with open(os.path.join(self.directory, "reader.stderr.log"), "a", encoding="utf-8") as evidence:
                evidence.write("response_read_failure\n")
                evidence.flush()
            return None

    def prune_ipc(self):
        # Runs after a callback was consumed. A file that is locked or already gone
        # is skipped now and retried by the next call; pruning never fails a callback.
        limit = self.sequence - self.ipc_retain
        while self.ipc_pruned < limit:
            number = self.ipc_pruned + 1
            try:
                for kind in ("request-", "response-"):
                    path = os.path.join(self.directory, kind + str(number) + ".json")
                    if os.path.exists(path):
                        os.remove(path)
            except OSError:
                return
            self.ipc_pruned = number

    def call(self, name, patch=None):
        self.touch_heartbeat()
        if self.process.poll() is not None:
            self.error = "worker_exited"
            return False
        self.sequence += 1
        request = {"id": self.sequence, "callback": name, "patch": patch, "state": self.ctx.state, "desired": self.ctx.desired, "deviceId": self.ctx.device_id, "version": self.ctx.payload_version}
        target = os.path.join(self.directory, "request-" + str(self.sequence) + ".json")
        with open(target + ".new", "w", encoding="utf-8") as f:
            json.dump(request, f)
        os.replace(target + ".new", target)
        deadline = time.monotonic() + self.timeout
        response_path = os.path.join(self.directory, "response-" + str(self.sequence) + ".json")
        read_failed = False
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.error = "worker_exited"
                return False
            if os.path.exists(response_path):
                try:
                    self.read_attempts += 1
                    result = self.read_response(response_path, deadline - time.monotonic())
                except (OSError, ValueError):
                    result = None
                if result is not None:
                    self.prune_ipc()
                    if not result["ok"]:
                        self.error = "payload_exception"
                        return False
                    for key, value in result["state"].items():
                        if key not in ["current", "lastGood", "ota", "assignedHub", "deviceId"]:
                            self.ctx.state[key] = value
                    for effect in result["effects"]:
                        if effect[0] == "telemetry":
                            self.ctx.send_telemetry(effect[1], effect[2])
                        elif effect[0] == "report":
                            self.ctx.report(effect[1])
                        elif effect[0] == "log":
                            self.ctx.log(effect[1])
                    self.error = None
                    return True
                self.read_failures += 1
                read_failed = True
            time.sleep(0.01)
        self.error = "response_read_failure" if read_failed else "watchdog_timeout"
        self.close()
        return False

    def close(self):
        self.heartbeat_stopped = True
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        if self in ACTIVE_WORKERS:
            ACTIVE_WORKERS.remove(self)
        # Capture threads drain independently. Do not close their pipes or wait
        # on logging here: cleanup must not consume the OTA method deadline.
        # Only the newest ipc_retain request/response pairs remain (see prune_ipc),
        # plus redacted output evidence, outside the repo.
