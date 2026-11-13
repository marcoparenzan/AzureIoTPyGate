# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
"""Fail-safe diagnostics only; never modifies wire data.

One daemon owns console and file I/O. Producers try a lock once and drop on
contention/full queue (dropped count is reported by the writer). Rotation is a
bounded ring of numbered overwrite slots, not rename/replace: preview.8 lacks
os.replace and Windows readers can deny writes. A denied slot is skipped until
the next event, with console still available. No files are deleted. Limits are
per logger/directory; use a separate state/log directory for each device.
"""
import json
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime

ARTIFACT_ROOT = "D:/Dev/BuildArtifacts/AzureIoTPyGate"
LEVELS = {"off": 0, "info": 1, "debug": 2, "trace": 3}
SENSITIVE = ("key", "secret", "token", "password", "pwd", "sas", "sig", "signature", "cert", "pem", "connectionstring", "privatekey")
REDACTED = "<redacted>"


def level_from(config=None, environ=None):
    config = config or {}
    environ = os.environ if environ is None else environ
    value = environ["PYGATE_LOG_LEVEL"] if "PYGATE_LOG_LEVEL" in environ else config.get("logLevel", "info")
    if not isinstance(value, str) or value.lower() not in LEVELS:
        # Do not echo an invalid value: it might itself be a secret.
        raise ValueError("Invalid log level; expected off, info, debug or trace (PYGATE_LOG_LEVEL > logLevel > info)")
    return value.lower()


def _inside(path):
    root = os.path.normcase(os.path.realpath(os.path.abspath(ARTIFACT_ROOT)))
    candidate = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    return candidate == root or candidate.startswith(root + os.sep)


def directory_from(config):
    explicit = config.get("logDir")
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit or not os.path.isabs(explicit) or not _inside(explicit):
            raise ValueError("logDir must be an absolute path under D:/Dev/BuildArtifacts/AzureIoTPyGate")
        return os.path.abspath(explicit)
    state = config.get("stateFile")
    if state and _inside(state):
        return os.path.join(os.path.dirname(os.path.abspath(state)), "logs")
    # One folder per device so two devices never share the same rotation files.
    device = config.get("registrationId")
    if isinstance(device, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
        return os.path.join(ARTIFACT_ROOT, "logs", device)
    return os.path.join(ARTIFACT_ROOT, "logs")


def sensitive(name):
    # Never invoke an arbitrary object's __str__ while diagnosing callbacks.
    return not isinstance(name, str) or any(part in name[:MAX_SCAN].lower() for part in SENSITIVE)


# Bound traversal, string scans and the queued line, not just queue length.
# Values beyond these budgets are never formatted or stringified.
MAX_DEPTH = 12
MAX_ITEMS = 128
MAX_NODES = 2048
MAX_SCAN = 16384
MAX_LINE = 32768


def _secrets(value, found, depth=0, budget=None, inherited=False):
    budget = [MAX_NODES] if budget is None else budget
    if depth > MAX_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1
    if inherited and isinstance(value, str):
        if value and len(found) < MAX_ITEMS:
            found.add(value[:MAX_SCAN])
    elif isinstance(value, dict):
        for index, (name, item) in enumerate(value.items()):
            if index >= MAX_ITEMS or budget[0] <= 0:
                break
            _secrets(item, found, depth + 1, budget, inherited or sensitive(name))
    elif isinstance(value, (list, tuple)):
        for item in value[:MAX_ITEMS]:
            if budget[0] <= 0:
                break
            _secrets(item, found, depth + 1, budget, inherited)


def _truncate(value, cap):
    return value if len(value) <= cap else value[:cap] + "...<truncated " + str(len(value) - cap) + " chars>"


def _string(value, secrets):
    original_length = len(value)
    value = value[:MAX_SCAN]
    # Remove URL credentials BEFORE replacing known secrets. Otherwise an
    # inserted '<redacted>' can prematurely terminate the URL matcher and leave
    # a query suffix/fragment exposed. Userinfo is also removed by _url.
    value = re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*://)[^\s\"<>]+", _url, value)
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, REDACTED)
    # Reject complete/incomplete PEM, connection strings and raw exception text.
    # Arbitrary exception objects are never stringified either.
    if re.search(r"(?i)-----BEGIN|-----END|SharedAccessSignature|SharedAccessKey\s*=|HostName\s*=|AccountKey\s*=|Endpoint\s*=|Traceback|\b[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)\s*:", value):
        return REDACTED
    value = re.sub(r"(?i)\b(?:[A-Za-z0-9_]*(?:privatekey|secret|token|password|pwd|sas|sig|signature|cert|pem)|key)\s*[\"']?\s*[:=]\s*[\"']?[^\s,;]+", REDACTED, value)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;]+", REDACTED, value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", REDACTED, value)
    # Unlabelled long credential-like blobs. sha256 remains visible (64 hex).
    value = re.sub(r"[A-Za-z0-9_+/=-]{80,}", REDACTED, value)
    if original_length > 256:
        return value[:256] + "...<truncated " + str(original_length - 256) + " chars>"
    return _truncate(value, 256)


def _url(match):
    value = match.group(0)
    scheme, rest = value.split("://", 1)
    clean = rest.split("?", 1)[0].split("#", 1)[0]
    authority, sep, path = clean.partition("/")
    authority = authority.rsplit("@", 1)[-1]
    return scheme + "://" + authority + sep + path + ("?<redacted>" if "?" in rest or "#" in rest else "")


def _redact(value, secrets, depth=0, budget=None):
    # A shared character budget avoids scanning MAX_SCAN for every leaf of a
    # large tree, or replacing every known secret in unbounded producer input.
    budget = [MAX_NODES, MAX_SCAN] if budget is None else budget
    if depth > MAX_DEPTH or budget[0] <= 0:
        return "<truncated structure>"
    budget[0] -= 1
    if isinstance(value, dict):
        result = {}
        for index, (name, item) in enumerate(value.items()):
            if index >= MAX_ITEMS or budget[0] <= 0:
                result["<truncated structure>"] = True
                break
            if not isinstance(name, str):
                name = "<non-string field>"
            if len(name) > budget[1]:
                result["<truncated field>"] = REDACTED
                continue
            budget[1] -= len(name)
            safe_name = _string(name, secrets)
            # Error fields are type/codes only, never raw exception text.
            result[safe_name] = REDACTED if sensitive(name) or name.lower() in ("error", "exception", "traceback", "exceptiontext") else _redact(item, secrets, depth + 1, budget)
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value[:MAX_ITEMS]:
            if budget[0] <= 0:
                break
            result.append(_redact(item, secrets, depth + 1, budget))
        if len(value) > len(result):
            result.append("<truncated structure>")
        return result
    if isinstance(value, str):
        if len(value) > budget[1]:
            return "...<truncated " + str(len(value)) + " chars>"
        budget[1] -= len(value)
        return _string(value, secrets)
    if value is None or isinstance(value, (bool, float)):
        return value
    if isinstance(value, int):
        return value if -10 ** 100 < value < 10 ** 100 else "<truncated number>"
    return REDACTED


def redact(value):
    try:
        found = set()
        _secrets(value, found)
        return _redact(value, found)
    except Exception:
        return REDACTED


class NullLogger:
    level = "off"
    def enabled(self, level):
        return False
    def emit(self, level, component, event, **fields):
        pass
    def message(self, direction, topic, payload, **fields):
        pass
    def close(self, timeout=2):
        pass


NULL = NullLogger()


def logger_for(owner):
    try:
        # New instrumentation attaches .diagnostics; .logger remains supported
        # for Context, Twin and existing integrations. Ignore unrelated metrics
        # methods named diagnostics (e.g. Runtime.diagnostics()).
        for name in ("diagnostics", "logger"):
            candidate = getattr(owner, name, None)
            if callable(getattr(candidate, "emit", None)) and callable(getattr(candidate, "message", None)):
                return candidate
    except Exception:
        pass
    return NULL


def emit(owner, level, component, event, **fields):
    # A diagnostic failure, even in a supplied logger, cannot escape.
    try:
        logger_for(owner).emit(level, component, event, **fields)
    except Exception:
        pass


def message(owner, direction, topic, payload, **fields):
    try:
        logger_for(owner).message(direction, topic, payload, **fields)
    except Exception:
        pass


class Logger:
    def __init__(self, config=None, environ=None, console=None, opener=None):
        config = config or {}
        self.level = level_from(config, environ)
        self.directory = directory_from(config)
        self.max_bytes = self._limit(config.get("logMaxBytes", 262144), 1024, 1048576)
        self.file_count = self._limit(config.get("logFileCount", 4), 1, 16)
        self.queue_size = self._limit(config.get("logQueueSize", 256), 1, 4096)
        # Fixed slots also bound growth across restarts. Names contain no data.
        self.path = os.path.join(self.directory, "agent.0.log")
        self.console = sys.stdout if console is None else console
        self.opener = open if opener is None else opener
        self.queue = deque()
        self.lock = threading.Lock()
        self.closing = False
        self.dropped = 0
        self.slot = 0
        self.written = 0
        self.thread = None
        if self.level != "off":
            try:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            except Exception:
                # No synchronous console/file fallback: callbacks must not block.
                self.thread = None
            if True:  # always say where the file is, so a quiet console is not mistaken for no logging
                self.emit("info", "log", "startup", file=self.path, rotation="numbered-overwrite", maxBytes=self.max_bytes, fileCount=self.file_count)

    @staticmethod
    def _limit(value, minimum, maximum):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError("Invalid logging bound")
        return value

    def enabled(self, level):
        try:
            return isinstance(level, str) and level in LEVELS and 0 < LEVELS[level] <= LEVELS[self.level]
        except Exception:
            return False

    def emit(self, level, component, event, **fields):
        try:
            if not self.enabled(level) or self.closing or self.thread is None:
                return
            original_body = fields.get("body")
            decoded_body = None
            if isinstance(original_body, str) and len(original_body) <= MAX_SCAN:
                try:
                    decoded_body = json.loads(original_body)
                    fields = dict(fields)
                    fields["body"] = decoded_body
                except Exception:
                    pass
            found = set()
            _secrets(fields, found)
            safe = _redact(fields, found)
            pairs = []
            for name, value in safe.items():
                if name == "body" and decoded_body is not None:
                    text = original_body if value == decoded_body else json.dumps(value, ensure_ascii=True)
                    text = text.replace("\r", "\\r").replace("\n", "\\n")
                else:
                    text = json.dumps(value, ensure_ascii=True) if not isinstance(value, str) else json.dumps(value, ensure_ascii=True)[1:-1]
                pairs.append(json.dumps(str(name))[1:-1] + "=" + _truncate(text, 4096))
            component = _string(component, found) if isinstance(component, str) else REDACTED
            event = _string(event, found) if isinstance(event, str) else REDACTED
            line = datetime.utcnow().isoformat() + "Z " + level.upper() + " " + json.dumps(component)[1:-1] + " " + json.dumps(event)[1:-1] + (" " + " ".join(pairs) if pairs else "")
            line = _truncate(line, MAX_LINE)
            if not self.lock.acquire(False):
                self.dropped += 1
                return
            try:
                if len(self.queue) >= self.queue_size:
                    self.dropped += 1
                else:
                    self.queue.append(line)
            finally:
                self.lock.release()
        except Exception:
            pass

    def message(self, direction, topic, payload, **fields):
        raw = None
        body = REDACTED
        try:
            if not self.enabled("debug"):
                return
            if isinstance(payload, bytes):
                raw = payload[:MAX_SCAN].decode("utf-8", errors="replace")
                size = len(payload)
                oversized = len(payload) > MAX_SCAN
            elif isinstance(payload, str):
                raw = payload[:MAX_SCAN]
                size = len(raw.encode("utf-8"))
                oversized = len(payload) > MAX_SCAN
            else:
                size, oversized = 0, False
            if not oversized:
                try:
                    body = json.loads(raw) if raw else None
                except Exception:
                    body = REDACTED
            else:
                raw = None
                body = "<truncated body>"
        except Exception:
            return
        try:
            kind = "telemetry" if topic.startswith("devices/") and "/messages/events/" in topic else "message"
            component = "mqtt"
            if topic.startswith("$iothub/twin/"):
                component = "twin"
                kind = "reported" if "/reported/" in topic else "GET" if "/GET/" in topic else "desired" if "/desired/" in topic else "response"
            elif topic.startswith("$iothub/methods/"):
                component, kind = "methods", "request" if "/POST/" in topic else "response"
            elif "/messages/devicebound/" in topic:
                kind = "C2D"
            elif topic.startswith("$dps/"):
                component = "dps"
            common = dict(fields)
            names = []
            if isinstance(body, dict):
                for name in body:
                    if len(names) >= MAX_ITEMS:
                        break
                    names.append(name)
            common.update({"direction": direction, "topic": topic, "size": size, "fields": sorted(names)})
            if oversized:
                common["sizeTruncated"] = True
            self.emit("debug", component, direction + " " + kind, **common)
            if self.enabled("trace"):
                # Keep the exact transport JSON formatting when redaction is a
                # no-op, otherwise serialize the recursively redacted body.
                common["body"] = raw if isinstance(raw, str) and body != REDACTED else json.dumps(body)
                self.emit("trace", component, direction + " " + kind, **common)
        except Exception:
            pass

    def _write(self, line):
        try:
            self.console.write(line + "\n")
            self.console.flush()
        except Exception:
            pass
        try:
            # Revalidate realpath before writing to reject junction/symlink
            # escapes. A hostile concurrent path swap remains an OS concern.
            if not _inside(self.directory):
                return
            os.makedirs(self.directory, exist_ok=True)
            data = (line + "\n").encode("utf-8")
            if len(data) > self.max_bytes:
                marker = b"...<truncated file line>\n"
                prefix = data[:self.max_bytes - len(marker)].decode("utf-8", errors="ignore").encode("utf-8")
                data = prefix + marker
            path = os.path.join(self.directory, "agent." + str(self.slot) + ".log")
            if self.written == 0 and os.path.exists(path):
                self.written = os.path.getsize(path)
            if self.written + len(data) > self.max_bytes:
                self.slot = (self.slot + 1) % self.file_count
                self.written = 0
                path = os.path.join(self.directory, "agent." + str(self.slot) + ".log")
            if not _inside(path):
                return
            mode = "wb" if self.written == 0 else "ab"
            with self.opener(path, mode) as stream:
                stream.write(data)
            self.written += len(data)
        except Exception:
            # Console is already written; never log exception text or retry I/O
            # in producer callbacks. Try again on a later queued event.
            pass

    def _run(self):
        while True:
            line = None
            try:
                with self.lock:
                    if self.queue:
                        line = self.queue.popleft()
                    dropped = self.dropped
                    self.dropped = 0
                    done = self.closing and not self.queue and line is None
                if dropped:
                    self._write(datetime.utcnow().isoformat() + "Z INFO log queue_dropped count=" + str(dropped))
                if line is not None:
                    self._write(line)
                elif done:
                    return
                else:
                    time.sleep(0.01)
            except Exception:
                if self.closing:
                    return
                time.sleep(0.01)

    def close(self, timeout=2):
        # Only shutdown/tests may wait; never invoke this from a callback.
        self.closing = True
        try:
            if self.thread is not None:
                self.thread.join(max(0, min(5, timeout)))
        except Exception:
            pass
