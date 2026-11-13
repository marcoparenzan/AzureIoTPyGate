# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import hashlib
import http.client
import json
import os
import re
import tempfile
import time
import urllib.parse
import pysharp_signing
from worker_runtime import PayloadProcess
from atomic_io import protected_write
from log import emit


class OtaError(Exception):
    pass


def atomic_write(path, data):
    protected_write(path, data)


def download_https(url, cap=262144, timeout=10, attempts=3, sleep=time.sleep, connection_factory=None, owner=None):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise OtaError("https_required")
    factory = connection_factory or http.client.HTTPSConnection
    for attempt in range(max(1, min(5, attempts))):
        connection = None
        started = time.monotonic()
        try:
            connection = factory(parts.hostname, parts.port or 443, timeout=timeout)
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            emit(owner, "debug", "ota", "OUT download", url=url, method="GET", attempt=attempt + 1)
            connection.request("GET", target)
            response = connection.getresponse()
            emit(owner, "debug", "ota", "IN download", status=response.status, latencyMs=round((time.monotonic() - started) * 1000, 3))
            if response.status != 200:
                if response.status == 429 or response.status >= 500:
                    raise ConnectionError("retryable")
                raise OtaError("http_status")
            length = response.getheader("Content-Length")
            if length and int(length) > cap:
                raise OtaError("size_cap")
            body = response.read(cap + 1)
            if len(body) > cap:
                raise OtaError("size_cap")
            return body
        except OtaError:
            raise
        except Exception:
            if attempt + 1 >= max(1, min(5, attempts)):
                emit(owner, "debug", "ota", "download_failed", attempt=attempt + 1)
                raise OtaError("download_failed")
            emit(owner, "debug", "ota", "download_retry", attempt=attempt + 1, delaySec=min(8, 2 ** attempt))
            sleep(min(8, 2 ** attempt))
        finally:
            if connection:
                connection.close()
    raise OtaError("download_failed")


class OtaController:
    def __init__(self, config, store, runtime, downloader=None, writer=atomic_write):
        self.config = config
        self.store = store
        store.protected_io = True
        self.runtime = runtime
        runtime.ota = self
        self.ctx = runtime.ctx
        self.root = os.path.join(os.path.dirname(os.path.abspath(store.path)), "payloads")
        self.worker_root = os.path.join(os.path.dirname(os.path.abspath(store.path)), "workers")
        self.downloader = downloader or (lambda url: download_https(url, int(config.get("otaSizeCap", 262144)), float(config.get("otaDownloadTimeoutSec", 10)), owner=self.ctx))
        self.writer = writer
        self.pending = None
        self.generation = 0
        self.status = "idle"
        self.error = None
        self.probation = 0
        self.history = []
        self.last_attempt = None
        self.active = None

    def transition(self, status, error=None, descriptor=None):
        previous = self.status
        self.status, self.error = status, error
        item = descriptor or self.active or self.store.data.get("current", {"name": "thermostat", "version": 1})
        # Preserve the reported error unchanged, but never write arbitrary
        # exception text supplied by a downloader/worker into diagnostics.
        reasons = {"no_initial_payload", "restore_failed", "no_last_good", "no_previous_good", "rollback_failed", "restart_during_probation", "superseded", "explicit_rollback", "method_timeout", "storage_error", "storage_or_payload_error", "invalid_name", "invalid_version", "invalid_size", "invalid_integrity_fields", "size_mismatch", "hash_mismatch", "signature_mismatch", "signature_verification_failed", "syntax_error", "version_conflict", "entry_points", "watchdog_timeout", "start_failed", "tick_failed", "worker_exited", "response_read_failure", "payload_exception", "payload_watchdog_timeout", "stored_hash_mismatch", "download_failed", "http_status", "size_cap", "https_required"}
        reason = error if error is None or isinstance(error, str) and error in reasons else "ota_failed"
        emit(self.ctx, "info", "ota", "state", previous=previous, status=status, reason=reason, name=item.get("name"), version=item.get("version"), probationTicks=self.probation)
        if previous == "probation" and status != "probation":
            emit(self.ctx, "info", "ota", "probation_end", status=status, reason=reason)
        self.history.append(status)
        self.history = self.history[-100:]
        good = self.store.data.get("lastGood", item)
        self.ctx.report({"payload": {"name": item["name"], "version": item["version"], "status": status, "lastGood": good["version"], "error": error, "updatedAt": self.ctx.now(), "downgrade": bool(item.get("downgrade", False))}})

    def save(self):
        self.store.data["ota"] = {"status": self.status}
        self.store.save()

    def descriptor(self, manifest):
        name = manifest.get("name")
        if not isinstance(name, str) or not re.match("^[A-Za-z0-9_-]{1,64}$", name):
            raise OtaError("invalid_name")
        version = manifest.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise OtaError("invalid_version")
        size = manifest.get("size")
        if not isinstance(size, int) or size < 1 or size > int(self.config.get("otaSizeCap", 262144)):
            raise OtaError("invalid_size")
        digest = manifest.get("sha256", "")
        signature = manifest.get("sig", "")
        if not re.match("^[a-fA-F0-9]{64}$", digest) or not re.match("^[a-fA-F0-9]{192}$", signature):
            raise OtaError("invalid_integrity_fields")
        return {"name": name, "version": version, "size": size, "sha256": digest.lower(), "sig": signature, "path": os.path.join(self.root, name, str(version) + ".py")}

    def verify(self, data, item):
        started = time.monotonic()
        check = "size"
        try:
            if len(data) != item["size"]:
                raise OtaError("size_mismatch")
            emit(self.ctx, "info", "ota", "check_result", check=check, result="passed", size=len(data))
            check = "hash"
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise OtaError("hash_mismatch")
            emit(self.ctx, "info", "ota", "check_result", check=check, result="passed", sha256=item["sha256"])
            check = "signature"
            try:
                with open(self.config["payloadPublicKeyPem"], encoding="utf-8") as f:
                    key = f.read()
                if not pysharp_signing.verify("ecdsa-p384-sha384", key, data, bytes.fromhex(item["sig"])):
                    raise OtaError("signature_mismatch")
            except OtaError:
                raise
            except Exception:
                raise OtaError("signature_verification_failed")
            emit(self.ctx, "info", "ota", "check_result", check=check, result="passed", length=len(item["sig"]))
            check = "syntax"
            try:
                compile(data.decode("utf-8").lstrip("\ufeff"), "payload", "exec")
            except Exception:
                raise OtaError("syntax_error")
            emit(self.ctx, "info", "ota", "check_result", check=check, result="passed")
        except Exception:
            emit(self.ctx, "info", "ota", "check_result", check=check, result="failed")
            raise
        finally:
            emit(self.ctx, "debug", "ota", "verification", latencyMs=round((time.monotonic() - started) * 1000, 3))

    def process(self, path):
        return PayloadProcess(path, self.ctx, self.worker_root, os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload_worker.py"), self.config.get("payloadWatchdogSec", 5), self.config.get("pysharpCommand", "pysharp"), self.config.get("workerHeartbeatTimeoutSec", 30), self.config.get("workerIpcRetain", 5))

    def safe_state(self, reason):
        # Keep transport/twin alive, but never retain a rejected payload.
        try:
            self.runtime.stop()
        except Exception:
            # Runtime clears its callback references even if worker close fails.
            self.ctx.log("worker_recovery_failed", "response_read_failure")
        self.active = None
        self.probation = 0
        self.runtime.errors += 1
        self.ctx.log("payload_error")
        self.ctx.report({"agent": {"payloadErrors": self.runtime.errors}})
        self.transition("failed", reason)

    def bootstrap(self):
        # Explicit outcomes: absence is not a failed restoration, and neither
        # authorizes Agent to execute a local file outside this hash gate.
        if "current" not in self.store.data:
            try:
                with open(self.runtime.payload_path, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                self.safe_state("no_initial_payload")
                return "no_payload"
            except Exception:
                self.safe_state("restore_failed")
                return "failed"
            try:
                item = {"name": "thermostat", "version": 1, "path": os.path.join(self.root, "thermostat", "1.py"), "sha256": hashlib.sha256(data).hexdigest(), "bootstrap": True}
                self.writer(item["path"], data)
                self.writer(item["path"] + ".sha256", item["sha256"].encode("ascii"))
                self.store.data["current"] = item
                self.store.data["lastGood"] = dict(item)
                self.restore(item)
                self.active = item
                self.transition("running")
                self.save()
                return "active"
            except Exception:
                self.safe_state("restore_failed")
                return "failed"
        current = self.store.data["current"]
        interrupted = self.store.data.get("ota", {}).get("status") in ["probation", "staged"]
        if interrupted:
            return "active" if self.rollback("restart_during_probation") else "failed"
        try:
            self.restore(current)
            self.active = current
            self.transition("running")
            return "active"
        except Exception:
            return "active" if self.rollback("restore_failed") else "failed"

    def restore(self, item):
        with open(item["path"], "rb") as f:
            data = f.read()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            emit(self.ctx, "info", "ota", "check_result", check="stored_hash", result="failed")
            raise OtaError("stored_hash_mismatch")
        emit(self.ctx, "info", "ota", "check_result", check="stored_hash", result="passed")
        if not item.get("bootstrap"):
            self.verify(data, item)
        worker = self.process(item["path"])
        if not worker.call("__validate__"):
            emit(self.ctx, "info", "ota", "check_result", check="entry_points", result="failed")
            worker.close()
            raise OtaError("entry_points")
        emit(self.ctx, "info", "ota", "check_result", check="entry_points", result="passed")
        self.runtime.activate_process(worker, item["path"], item["version"])
        emit(self.ctx, "info", "ota", "activated", name=item["name"], version=item["version"], source="restore")
        if not self.runtime.call("on_start"):
            raise OtaError("start_failed")

    def submit(self, manifest):
        try:
            emit(self.ctx, "info", "ota", "manifest_received", name=manifest.get("name"), version=manifest.get("version"), size=manifest.get("size"), sha256=manifest.get("sha256"))
            emit(self.ctx, "trace", "ota", "manifest", body=manifest)
        except Exception:
            pass
        self.generation += 1
        self.pending = json.loads(json.dumps(manifest))

    def poll(self):
        if self.pending is None:
            return
        started = time.monotonic()
        manifest = self.pending
        self.pending = None
        generation = self.generation
        item = None
        candidate = None
        swapped = False
        phase = "manifest"
        try:
            item = self.descriptor(manifest)
            emit(self.ctx, "info", "ota", "check_result", check="manifest", result="passed", name=item["name"], version=item["version"], size=item["size"], sha256=item["sha256"], length=len(item["sig"]))
            phase = "policy"
            fingerprint = (item["name"], item["version"], item["sha256"], item["sig"])
            current = self.store.data.get("current", {"name": item["name"], "version": 0})
            if item["name"] == current["name"] and item["version"] == current["version"]:
                if item["sha256"] != current["sha256"]:
                    raise OtaError("version_conflict")
                emit(self.ctx, "debug", "ota", "manifest_skipped", reason="already_current", generation=generation)
                return
            if fingerprint == self.last_attempt:
                emit(self.ctx, "debug", "ota", "manifest_skipped", reason="duplicate_attempt", generation=generation)
                return
            self.last_attempt = fingerprint
            if self.status == "probation":
                self.rollback("superseded")
            self.transition("downloading", descriptor=item)
            phase = "download"
            download_started = time.monotonic()
            data = self.downloader(manifest["url"])
            emit(self.ctx, "debug", "ota", "download_result", size=len(data), latencyMs=round((time.monotonic() - download_started) * 1000, 3))
            if generation != self.generation:
                emit(self.ctx, "debug", "ota", "candidate_skipped", reason="superseded", generation=generation)
                return
            self.transition("verifying", descriptor=item)
            phase = "verification"
            self.verify(data, item)
            # Candidate module initialization/entry checks run in a killable process.
            phase = "entry_points"
            validation_path = os.path.join(self.worker_root, "candidate-" + str(generation) + ".py")
            self.writer(validation_path, data)
            candidate = self.process(validation_path)
            if not candidate.call("__validate__"):
                raise OtaError("entry_points" if candidate.error != "watchdog_timeout" else "watchdog_timeout")
            emit(self.ctx, "info", "ota", "check_result", check="entry_points", result="passed")
            if generation != self.generation:
                emit(self.ctx, "debug", "ota", "candidate_skipped", reason="superseded", generation=generation)
                return
            phase = "storage"
            if os.path.exists(item["path"]):
                with open(item["path"], "rb") as f:
                    if f.read() != data:
                        raise OtaError("version_conflict")
            self.writer(item["path"], data)
            self.writer(item["path"] + ".sha256", item["sha256"].encode("ascii"))
            item["downgrade"] = item["version"] < current["version"]
            self.active = item
            self.transition("staged")
            self.store.data["current"] = item
            # Crash between state persistence and swap must recover lastGood.
            self.status = "probation"
            self.save()
            phase = "activation"
            self.runtime.activate_process(candidate, item["path"], item["version"])
            candidate = None
            swapped = True
            emit(self.ctx, "info", "ota", "activated", name=item["name"], version=item["version"], downgrade=item["downgrade"])
            self.probation = max(1, min(100, int(manifest.get("probationTicks", self.config.get("probationTicks", 10)))))
            self.transition("probation")
            emit(self.ctx, "info", "ota", "probation_start", ticks=self.probation, version=item["version"])
            if not self.runtime.call("on_start"):
                if self.runtime.last_error not in ["worker_exited", "watchdog_timeout", "response_read_failure"]:
                    raise OtaError("watchdog_timeout" if self.runtime.last_error == "payload_watchdog_timeout" else self.runtime.last_error or "start_failed")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, OtaError) else "storage_or_payload_error"
            emit(self.ctx, "info", "ota", "phase_failed", phase=phase)
            if swapped:
                self.rollback(reason)
            else:
                if "lastGood" in self.store.data and item and self.store.data.get("current", {}).get("version") == item["version"]:
                    self.store.data["current"] = dict(self.store.data["lastGood"])
                self.transition("failed", reason, item)
        finally:
            emit(self.ctx, "debug", "ota", "apply_result", generation=generation, status=self.status, latencyMs=round((time.monotonic() - started) * 1000, 3))
            if candidate:
                candidate.close()

    def tick_result(self, result):
        if self.status != "probation" or result is None:
            return
        if not result:
            # Infrastructure failures leave probation/current/lastGood intact.
            # A subsequent tick can recover; they do not condemn the artifact.
            if self.runtime.last_error in ["worker_exited", "watchdog_timeout", "response_read_failure"]:
                return
            self.rollback("watchdog_timeout" if self.runtime.last_error == "payload_watchdog_timeout" else self.runtime.last_error or "tick_failed")
            return
        self.probation -= 1
        if self.probation <= 0:
            previous_good = self.store.data["lastGood"]
            previous_history = self.store.data.get("previousGood")
            self.store.data["previousGood"] = dict(previous_good)
            self.store.data["lastGood"] = dict(self.active)
            self.transition("running")
            try:
                self.save()
            except Exception:
                self.store.data["lastGood"] = previous_good
                if previous_history is None:
                    self.store.data.pop("previousGood", None)
                else:
                    self.store.data["previousGood"] = previous_history
                self.rollback("storage_error")
                return
            # Housekeeping only: a failed prune never undoes an accepted update.
            try:
                self.prune()
            except Exception:
                emit(self.ctx, "info", "ota", "prune_failed")

    def rollback(self, reason="explicit_rollback"):
        accepted = self.store.data.get("lastGood")
        good = accepted
        explicit_accepted = False
        if reason == "explicit_rollback":
            current = self.store.data.get("current")
            # During probation lastGood is still the previous accepted version.
            # Once accepted, reloading lastGood would be a dishonest no-op.
            probation_candidate = self.status == "probation" and current != accepted
            if not probation_candidate:
                good = self.store.data.get("previousGood")
                explicit_accepted = True
            if not good or good == current:
                self.transition("failed", "no_previous_good")
                return False
        if not good:
            self.safe_state("no_last_good")
            return False
        try:
            self.restore(good)
            self.store.data["current"] = dict(good)
            self.active = dict(good)
            if explicit_accepted:
                self.store.data["lastGood"] = dict(good)
                if accepted:
                    self.store.data["previousGood"] = dict(accepted)
            self.probation = 0
            self.transition("rolledBack", reason)
            self.save()
            return True
        except Exception:
            self.safe_state("rollback_failed")
            return False

    def prune(self):
        # Never delete generated artifacts: archive older versions under <name>/archive.
        directory = os.path.join(self.root, self.active["name"])
        versions = sorted([int(n[:-3]) for n in os.listdir(directory) if n.endswith(".py") and n[:-3].isdigit()], reverse=True)
        keep = set()
        for item in [self.store.data.get("current"), self.store.data.get("lastGood")]:
            if item and item["name"] == self.active["name"]:
                keep.add(item["version"])
        for version in versions:
            if len(keep) < 3:
                keep.add(version)
        # Archive beside the artifacts: os.replace cannot cross volumes, and the artifact
        # root may live on a different drive than any fixed trash path.
        archive = os.path.join(directory, "archive")
        for version in versions:
            if version in keep:
                continue
            for suffix in [".py", ".py.sha256"]:
                source = os.path.join(directory, str(version) + suffix)
                if os.path.exists(source):
                    os.makedirs(archive, exist_ok=True)
                    target = os.path.join(tempfile.mkdtemp(prefix="ota-archive-", dir=archive), self.active["name"] + "-" + str(version) + suffix)
                    os.replace(source, target)

    def apply_sync(self, manifest, timeout=60):
        # The deadline covers the whole method, including download, verification,
        # staging and worker startup, not only the probation ticks that follow.
        deadline = time.monotonic() + max(0.2, min(300, float(timeout)))
        self.submit(manifest)
        self.poll()
        while self.status == "probation" and time.monotonic() < deadline:
            self.tick_result(self.runtime.tick())
            time.sleep(0.01)
        if self.status == "probation":
            self.rollback("method_timeout")
        current = self.store.data.get("current", {})
        success = self.status == "running" and current.get("version") == manifest.get("version") and current.get("name") == manifest.get("name")
        return (200 if success else 409), {"status": self.status, "error": self.error}
