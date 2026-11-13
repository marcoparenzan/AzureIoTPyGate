# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
"""Offline logging regressions. Secret sentinels and captured output stay in RAM.

Only the rotation case writes real logs (non-secret synthetic data only). Each
case has its own GUID directory outside the repository. Integration cases call
actual Context/LoopbackTransport/Twin methods, NOT logger.message themselves;
missing instrumentation is a failure, never a skip or synthetic substitute.
"""
import json
import os
import re
import threading
import time
from System import Guid
from log import Logger, NULL, REDACTED, directory_from, emit, level_from, logger_for, message, redact
from mqtt_transport import LoopbackTransport
from runtime import Context
from twin import Twin

ARTIFACT_TMP = "D:/Dev/BuildArtifacts/AzureIoTPyGate/tmp/logging"


def scratch():
    # Do not trust a caller's working directory or repository-local scratch.
    directory = os.path.join(ARTIFACT_TMP, str(Guid.NewGuid().ToString("N")))
    os.makedirs(directory, exist_ok=True)
    return directory


def configuration(level="trace", directory=None):
    directory = directory or scratch()
    return {"logLevel": level, "logDir": os.path.join(directory, "logs"),
            "stateFile": os.path.join(directory, "state.json"),
            "logQueueSize": 1024, "logMaxBytes": 65536, "logFileCount": 3}


class Console:
    def __init__(self):
        self.parts = []
    def write(self, text):
        self.parts.append(text)
    def flush(self):
        pass
    def text(self):
        return "".join(self.parts)


class MemoryFiles:
    """Binary rotating-file contract, without ever persisting test secrets."""
    def __init__(self):
        self.files = {}
        self.calls = 0
    def open(self, path, mode):
        self.calls += 1
        if mode == "wb":
            self.files[path] = b""
        assert mode in ("wb", "ab"), "unexpected log file mode"
        owner = self
        class Stream:
            def __enter__(self):
                return self
            def __exit__(self, kind, value, traceback):
                return False
            def write(self, data):
                owner.files[path] = owner.files.get(path, b"") + data
                return len(data)
        return Stream()
    def text(self):
        return "".join(data.decode("utf-8") for data in self.files.values())


def captured(config=None):
    console, files = Console(), MemoryFiles()
    logger = Logger(config or configuration(), environ={}, console=console, opener=files.open)
    assert logger.level == "off" or logger.thread is not None, "logging writer unavailable"
    return logger, console, files


def finish(logger):
    logger.close(5)
    assert logger.thread is None or not logger.thread.is_alive(), "logging writer did not drain"


def test_log_levels_precedence_invalid():
    assert level_from({}, {}) == "info"
    assert level_from({"logLevel": "debug"}, {}) == "debug"
    assert level_from({"logLevel": "invalid"}, {"PYGATE_LOG_LEVEL": "TRACE"}) == "trace"
    assert level_from({"logLevel": "trace"}, {"PYGATE_LOG_LEVEL": "off"}) == "off"
    for invalid in ("", "verbose", " info ", None, 3, False):
        for environment in (False, True):
            try:
                level_from({"logLevel": invalid}, {"PYGATE_LOG_LEVEL": invalid} if environment else {})
                assert False, "invalid level accepted"
            except ValueError as failure:
                assert "expected off, info, debug or trace" in str(failure), "unclear level error"
    for selected, allowed in (("off", []), ("info", ["info"]),
                              ("debug", ["info", "debug"]), ("trace", ["info", "debug", "trace"])):
        logger, console, files = captured(configuration(selected))
        try:
            for level in ("info", "debug", "trace"):
                logger.emit(level, "case", "level_" + level, value=7)
            logger.message("OUT", "devices/offline/messages/events/", '{"temperature":21.4}', qos=1, mid=17)
        finally:
            finish(logger)
        for text in (console.text(), files.text()):
            for level in ("info", "debug", "trace"):
                assert ("case level_" + level in text) == (level in allowed), "level filter mismatch"
            assert ("body=" in text) == (selected == "trace"), "trace body filter mismatch"
            assert ("OUT telemetry" in text) == (selected in ("debug", "trace")), "summary filter mismatch"
            for line in text.splitlines():
                assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z (INFO|DEBUG|TRACE) ", line), "UTC single-line format mismatch"
        if selected == "off":
            assert files.calls == 0 and console.text() == "", "off performed output"
        elif selected in ("debug", "trace"):
            assert json.dumps(logger.path)[1:-1] in console.text(), "startup file location missing"


def test_log_rotation_caps():
    config = configuration("info")
    config.update({"logMaxBytes": 1024, "logFileCount": 3})
    # Same directory, two process-equivalent logger instances; existing slots
    # must still stay bounded after restart. No rename/replace dependency.
    for run in range(2):
        logger = Logger(config, environ={}, console=Console())
        try:
            for index in range(90):
                logger.emit("info", "case", "rotation", run=run, index=index,
                            body={"temperature": 21.4, "description": "visible prose " * 20})
            logger.emit("info", "case", "wide_line", **{"value" + str(i): "visible prose " * 20 for i in range(30)})
            logger.emit("info", "case", "unicode_line", body=json.dumps({"description": "\u2603 " * 110}, ensure_ascii=False))
        finally:
            finish(logger)
        paths = os.listdir(logger.directory)
        assert len(paths) == 3, "rotation did not use exactly bounded slots"
        total = 0
        for name in paths:
            assert name in ("agent.0.log", "agent.1.log", "agent.2.log"), "unexpected rotation file"
            path = os.path.join(logger.directory, name)
            size = os.path.getsize(path)
            assert 0 < size <= 1024, "rotation byte cap exceeded"
            total += size
            with open(path, "rb") as stream:
                data = stream.read()
            assert data.endswith(b"\n"), "rotation lost line terminator"
            data.decode("utf-8")
        assert total <= 3072, "total rotation cap exceeded"
    assert os.path.normcase(os.path.abspath(directory_from({"stateFile": config["stateFile"]}))) == os.path.normcase(os.path.abspath(config["logDir"])), "state-relative default mismatch"
    # Without logDir or an in-root state file, each device gets its own folder.
    per_device = os.path.normcase(directory_from({"registrationId": "device01"})).replace("\\", "/")
    assert per_device.endswith("/logs/device01"), per_device
    for odd in ("../x", "a/b", "", None, "x" * 65):
        assert os.path.normcase(directory_from({"registrationId": odd})).replace("\\", "/").endswith("/logs"), odd
    for directory in ("logs", "C:/outside/logs", ARTIFACT_TMP + "/../../../../outside"):
        try:
            directory_from({"logDir": directory})
            assert False, "unsafe logDir accepted"
        except ValueError:
            pass


def attach(transport, logger):
    # .diagnostics is the new instrumentation attachment; .logger keeps the
    # existing public interface usable during round4's coordinated transition.
    transport.diagnostics = logger
    transport.logger = logger
    assert logger_for(transport) is logger, "transport diagnostic attachment ignored"


def test_log_failures_safe():
    def denied(path, mode):
        raise OSError("synthetic sharing violation")
    console = Console()
    logger = Logger(configuration("info"), environ={}, console=console, opener=denied)
    transport = LoopbackTransport()
    attach(transport, logger)
    twin = Twin(transport)
    context = Context("offline-test", transport, twin, {})
    try:
        logger.emit("info", "case", "file_failure")
        transport.connect("loopback.invalid", "offline-test")
        context.send_telemetry({"temperature": 22.5})
        # Formatting errors, cyclic structures and bad application objects
        # must neither stringify exceptions nor escape into the caller.
        cycle = {}
        cycle["nested"] = cycle
        logger.emit("info", "case", "cycle", body=cycle)
        logger.emit("info", "case", "exception", exception=RuntimeError("synthetic failure"))
        logger.message("IN", None, object())
        logger.emit([], object(), object())
        assert len(transport.published) == 1, "logging interrupted transport"
    finally:
        finish(logger)
    assert "file_failure" in console.text(), "file failure lost console fallback"
    assert "synthetic sharing violation" not in console.text(), "raw file exception exposed"
    class BrokenConsole:
        def write(self, text):
            raise OSError("synthetic console failure")
        def flush(self):
            raise OSError("synthetic console failure")
    files = MemoryFiles()
    logger = Logger(configuration("info"), environ={}, console=BrokenConsole(), opener=files.open)
    try:
        logger.emit("info", "case", "console_failure")
    finally:
        finish(logger)
    assert "console_failure" in files.text(), "console failure lost file output"
    class BrokenDiagnostics:
        def emit(self, *args, **kwargs):
            raise RuntimeError("synthetic diagnostic failure")
        def message(self, *args, **kwargs):
            raise RuntimeError("synthetic diagnostic failure")
    attach(transport, BrokenDiagnostics())
    emit(transport, "info", "case", "safe")
    message(transport, "OUT", "devices/offline/messages/events/", "{}")
    assert logger_for(object()) is NULL, "missing logger did not default to null"
    class Hostile:
        def __str__(self):
            raise RuntimeError("must not stringify")
    assert redact(Hostile()) == REDACTED, "unknown object was stringified"


def test_log_nonblocking_bounded_queue():
    entered, release = threading.Event(), threading.Event()
    console = Console()
    class BlockedConsole:
        def write(self, text):
            entered.set()
            release.wait(10)
            console.write(text)
        def flush(self):
            pass
    files = MemoryFiles()
    config = configuration("info")
    config["logQueueSize"] = 2
    logger = Logger(config, environ={}, console=BlockedConsole(), opener=files.open)
    try:
        logger.emit("info", "case", "writer_blocked")
        assert entered.wait(5), "writer did not reach slow sink"
        start = time.monotonic()
        for i in range(40):
            logger.emit("info", "case", "producer", index=i)
        assert time.monotonic() - start < 2, "producer waited for blocked I/O"
        assert len(logger.queue) == 2 and logger.dropped >= 38, "queue was not bounded"
        # Producer must try its lock once rather than wait for writer contention.
        logger.lock.acquire()
        try:
            start = time.monotonic()
            logger.emit("info", "case", "contended")
            assert time.monotonic() - start < 1, "producer waited on writer lock"
        finally:
            logger.lock.release()
    finally:
        release.set()
        finish(logger)
    assert "queue_dropped" in console.text(), "dropped count was not reported"


def secret_patch():
    # Unique synthetic values exist only in memory, including on failure.
    sentinels = ["R4_" + str(Guid.NewGuid().ToString("N")) for i in range(6)]
    patch = {"$version": 5, "targetTemperature": 22.0,
             "nested": {"sasToken": sentinels[0], "privateKey": sentinels[1],
                        "downloadUrl": "https://offline.invalid/fw.py?sig=" + sentinels[2] + "&other=" + sentinels[0] + "#" + sentinels[3],
                        "items": [{"password": sentinels[4], "visible": "desired-visible"}],
                        "veryLong": "visible prose " * 40 + sentinels[5]},
             "manifest": {"name": "thermostat", "version": 2, "size": 96,
                          "sha256": "a" * 64, "signature": sentinels[4]}}
    return patch, sentinels


def assert_redacted(console, files, sentinels):
    for text in (console.text(), files.text()):
        assert text, "no output captured"
        for sentinel in sentinels:
            # Never include actual captured output/sentinels in a failure message.
            assert sentinel not in text, "secret redaction regression"
        assert REDACTED in text, "redaction marker missing"
        assert "desired-visible" in text and "targetTemperature" in text, "non-secret desired data disappeared"
        assert "https://offline.invalid/fw.py?<redacted>" in text, "URL path/marker mismatch"
        assert "...<truncated " in text, "long string not truncated"
        assert "thermostat" in text and "a" * 64 in text, "non-secret manifest metadata disappeared"


def test_log_nested_redaction_console_file():
    logger, console, files = captured()
    patch, sentinels = secret_patch()
    try:
        logger.message("IN", "$iothub/twin/PATCH/properties/desired/?$version=5", json.dumps(patch))
        # A known secret also used in a public field or an event label is still
        # redacted, and replacing it must not expose the remainder of a URL.
        logger.emit("trace", "case", sentinels[0], body=patch, alias=sentinels[0])
        logger.emit("trace", "case", "free_text", pemText="-----BEGIN PRIVATE KEY-----\n" + sentinels[1],
                    authorization="Bearer " + sentinels[2], exception=RuntimeError(sentinels[3]),
                    error=sentinels[4], detail="RuntimeError: " + sentinels[5])
        logger.message("OUT", "devices/offline/messages/events/", '{"temperature":21.4,"deviceId":"offline"}')
        # Body cap is separate from the per-string cap.
        logger.emit("trace", "case", "large_body", body={"value" + str(i): "visible prose " * 15 for i in range(40)})
    finally:
        finish(logger)
    assert_redacted(console, files, sentinels)
    for text in (console.text(), files.text()):
        assert 'body={"temperature":21.4,"deviceId":"offline"}' in text, "non-secret telemetry JSON changed"
        assert "BEGIN PRIVATE KEY" not in text and "RuntimeError:" not in text, "PEM/exception exposed"
        assert "Bearer " not in text, "authorization credential exposed"
        wide = [line for line in text.splitlines() if "large_body" in line]
        assert len(wide) == 1 and len(wide[0].split("body=", 1)[1]) < 4160, "body cap exceeded"


def test_log_transport_trace_desired_changed():
    logger, console, files = captured()
    transport = LoopbackTransport()
    attach(transport, logger)
    twin = Twin(transport)
    context = Context("offline-test", transport, twin, {})
    transport.on_message = twin.message
    patch, sentinels = secret_patch()
    try:
        transport.connect("loopback.invalid", "offline-test")
        telemetry = {"deviceId": "offline-test", "temperature": 21.4, "samples": [20, 21]}
        context.send_telemetry(telemetry, {"source": "offline"})
        topic, raw = transport.published[-1]
        assert json.loads(raw) == telemetry, "logging modified telemetry wire data"
        transport.inject_desired(patch)
        transport.poll(0)
        assert twin.version == 5, "desired patch was not applied"
        transport.inject_desired({"$version": 6, "targetTemperature": 23.0})
        transport.poll(0)
        assert twin.version == 6, "second desired version was not applied"
    finally:
        finish(logger)
    assert_redacted(console, files, sentinels)
    for text in (console.text(), files.text()):
        outgoing = [line for line in text.splitlines() if "TRACE" in line and "OUT telemetry" in line]
        assert len(outgoing) == 1, "transport telemetry trace missing/duplicated"
        assert "topic=" + topic in outgoing[0] and "qos=1" in outgoing[0], "transport trace metadata missing"
        assert "size=" + str(len(raw.encode("utf-8"))) in outgoing[0], "transport trace size mismatch"
        assert "body=" + raw in outgoing[0], "trace differs from actual published JSON"
        incoming = [line for line in text.splitlines() if "TRACE" in line and "IN desired" in line and '"$version": 6' in line]
        assert incoming, "incoming desired version/body trace missing"
        # The changed-key event may be separate from the message-body trace.
        changes = [line for line in text.splitlines() if "changed=" in line and "targetTemperature" in line and ("$version=6" in line or "version=6" in line or '"$version": 6' in line)]
        assert changes, "desired changed-key/version diagnostic missing"
        assert all("nested" not in line.split("changed=", 1)[1].split(" body=", 1)[0] for line in changes), "unchanged desired keys reported as changed"
    # Only already-checked non-secret telemetry and the second simple desired
    # patch reach the sample evidence file; sentinel captures remain in RAM.
    sample = os.path.join("D:/Dev/BuildArtifacts/AzureIoTPyGate/logs", "round4-sample.log")
    with open(sample, "w", encoding="utf-8") as stream:
        stream.write(outgoing[0] + "\n" + incoming[-1] + "\n" + changes[-1] + "\n")


LOG_TESTS = [test_log_levels_precedence_invalid, test_log_rotation_caps,
             test_log_failures_safe, test_log_nonblocking_bounded_queue,
             test_log_nested_redaction_console_file, test_log_transport_trace_desired_changed]
