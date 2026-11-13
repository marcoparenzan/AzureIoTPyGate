# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import time
import urllib.parse
import os
import ssl
import base64
import http.client
import hashlib
from datetime import datetime, timezone
from mqtt_transport import query
from log import emit, message


def certificate_renewal_at(cert, issued_at, config):
    path = config.get("certificateExpiryFile", cert[:-4] + ".expiry.json" if cert.endswith(".pem") else cert + ".expiry.json")
    fallback = issued_at + config.get("adrRenewAfterSeconds", 60 * 86400)
    if not os.path.exists(path):
        return fallback
    with open(path, encoding="utf-8") as f:
        metadata = json.load(f)
    with open(cert, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if metadata.get("certificateSha256") != digest:
        # Stale metadata from a previous issuance must not govern the new certificate.
        return fallback
    value = metadata.get("notAfter", "")
    if len(value) != 20 or value[4] != "-" or value[7] != "-" or value[10] != "T" or value[13] != ":" or value[16] != ":" or value[19] != "Z":
        raise ValueError("Certificate notAfter must be UTC yyyy-MM-ddTHH:mm:ssZ")
    expiry = datetime(int(value[0:4]), int(value[5:7]), int(value[8:10]), int(value[11:13]), int(value[14:16]), int(value[17:19]), tzinfo=timezone.utc).timestamp()
    return expiry - config.get("adrRenewBeforeSeconds", 7 * 86400)


def verified_https_connection(host, context, timeout=30, factory=None):
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise ValueError("DPS requires server certificate and hostname verification")
    factory = factory or http.client.HTTPSConnection
    connection = factory(host, 443, context=context, timeout=timeout)
    # PySharp preview.8 ignores constructor context; connect() reads _context.
    connection._context = context
    return connection


class HttpsDpsClient:
    def __init__(self, config):
        self.config = config
        self.context = ssl.create_default_context()
        self.context.load_cert_chain(config["bootstrapCert"], config["bootstrapKey"])

    def request(self, method, path, body):
        c = verified_https_connection(self.config["globalEndpoint"], self.context)
        try:
            c.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = c.getresponse()
            return response.status, response.read(), response.getheader("retry-after")
        finally:
            c.close()


def save_raw(path, raw):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + ".new", "wb") as f:
        f.write(raw)
    os.replace(path + ".new", path)


def issued_bundle(items, key, folder):
    # No X.509 parser: TLS loading establishes which cert matches the key.
    if not isinstance(items, list) or not items or len(items) > 10:
        raise ValueError("Invalid issued certificate list")
    os.makedirs(folder, exist_ok=True)
    pems = []
    matches = []
    for i in range(len(items)):
        item = items[i]
        if not isinstance(item, str):
            raise ValueError("Invalid certificate encoding")
        if item.strip().startswith("-----BEGIN CERTIFICATE-----"):
            pem = item.strip() + "\n"
        else:
            decoded = base64.b64decode(item)
            if decoded.startswith(b"-----BEGIN CERTIFICATE-----"):
                pem = decoded.decode("utf-8").strip() + "\n"
            else:
                encoded = base64.b64encode(decoded).decode("ascii")
                pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(encoded[j:j+64] for j in range(0, len(encoded), 64)) + "\n-----END CERTIFICATE-----\n"
        path = os.path.join(folder, "issued-candidate-" + str(i) + ".pem")
        save_raw(path, pem.encode("utf-8"))
        try:
            ssl.create_default_context().load_cert_chain(path, key)
            matches.append(i)
        except ssl.SSLError:
            pass
        pems.append(pem)
    if len(matches) != 1:
        raise ValueError("Issued chain must contain exactly one key-matching leaf")
    leaf = matches[0]
    bundle = pems[leaf] + "".join(pems[i] for i in range(len(pems)) if i != leaf)
    target = os.path.join(folder, "issued-chain.pem")
    # Validate staged bundle before replacing the previous operational bundle.
    save_raw(target + ".staged", bundle.encode("utf-8"))
    ssl.create_default_context().load_cert_chain(target + ".staged", key)
    os.replace(target + ".staged", target)
    return target


class Provisioner:
    def __init__(self, config, state, transport, clock=time.monotonic, sleep=time.sleep):
        self.config = config
        self.state = state
        self.transport = transport
        self.logger = getattr(transport, "logger", None)
        self.clock = clock
        self.sleep = sleep
        self.responses = []
        self.rid = 0

    def clear_assignment(self):
        self.state.pop("assignedHub", None)
        self.state.pop("deviceId", None)

    def _message(self, topic, payload):
        if topic.startswith("$dps/registrations/res/"):
            self.responses.append((topic, json.loads(payload)))

    def renewal_due(self):
        if self.config.get("identityFamily", "X509Dps") != "X509DpsAdr":
            return False
        cert = self.state.get("issuedCert")
        if cert and os.path.exists(cert):
            issued_at = self.state.get("adrIssuedAt")
            if issued_at is not None:
                self.state["adrRenewAt"] = certificate_renewal_at(cert, issued_at, self.config)
        return time.time() >= self.state.get("adrRenewAt", 0)

    def _apply_operational_certificate(self, cert):
        if hasattr(self.transport, "set_certificate"):
            self.transport.set_certificate(cert, self.config["deviceKey"])

    def _register_adr(self, timeout):
        cert = self.state.get("issuedCert")
        if self.state.get("assignedHub") and self.state.get("deviceId") and cert and os.path.exists(cert) and not self.renewal_due():
            self._apply_operational_certificate(cert)
            emit(self, "info", "dps", "cached_assignment", host=self.state["assignedHub"], deviceId=self.state["deviceId"], registration="skipped")
            return self.state["assignedHub"], self.state["deviceId"]
        if not self.config.get("allowDpsRegistration", False):
            raise PermissionError("DPS registration requires explicit permission")
        request = getattr(self.transport, "dps_https_request", None)
        if request is None:
            request = HttpsDpsClient(self.config).request
        with open(self.config["csr"], encoding="utf-8") as f:
            pem = f.read()
        csr = "".join(line.strip() for line in pem.splitlines() if not line.startswith("-----"))
        if not csr:
            raise ValueError("Missing CSR")
        registration = self.config["registrationId"]
        base = "/" + urllib.parse.quote(self.config["idScope"], safe="") + "/registrations/" + urllib.parse.quote(registration, safe="")
        api = "?api-version=2026-11-01"
        folder = os.path.dirname(os.path.abspath(self.config["deviceKey"]))
        deadline = self.clock() + timeout
        method, path, body = "PUT", base + "/register" + api, json.dumps({"registrationId": registration, "csr": csr})
        poll = 0
        while self.clock() < deadline:
            request_started = self.clock()
            # CSR is public certificate material, but still keep it out of
            # diagnostic bodies; the centralized logger redacts this field.
            diagnostic = {"registrationId": registration, "certificateCsr": csr} if method == "PUT" else ""
            message(self, "OUT", "$dps/https" + path, json.dumps(diagnostic) if isinstance(diagnostic, dict) else diagnostic, method=method, host=self.config["globalEndpoint"])
            status, raw, retry = request(method, path, body)
            message(self, "IN", "$dps/https" + path, raw, method=method, status=status, latencyMs=round((self.clock() - request_started) * 1000, 3))
            save_raw(os.path.join(folder, "dps-registration-raw.json"), raw)
            if status not in [200, 202]:
                raise ConnectionError("ADR registration rejected: " + str(status))
            response = json.loads(raw.decode("utf-8"))
            save_raw(os.path.join(folder, "dps-response-" + str(poll) + ".json"), raw)
            poll += 1
            result = response.get("registrationState", response)
            if response.get("status", result.get("status")) == "assigned":
                hub, device = result.get("assignedHub"), result.get("deviceId")
                if not hub or device != registration:
                    raise ValueError("Incomplete or mismatched ADR assignment")
                cert = issued_bundle(result.get("issuedCertificateChain"), self.config["deviceKey"], folder)
                self._apply_operational_certificate(cert)
                issued_at = time.time()
                self.state.update({"assignedHub": hub, "deviceId": device, "issuedCert": cert, "adrIssuedAt": issued_at, "adrRenewAt": certificate_renewal_at(cert, issued_at, self.config)})
                return hub, device
            if response.get("status", result.get("status")) == "failed":
                raise ConnectionError("ADR assignment failed")
            operation = response.get("operationId")
            if not operation:
                raise ValueError("Missing ADR operation id")
            delay = max(1, min(60, int(retry or "2")))
            emit(self, "debug", "dps", "poll_scheduled", delaySec=delay, status=status, attempt=poll)
            self.sleep(delay)
            method, path, body = "GET", base + "/operations/" + urllib.parse.quote(operation, safe="") + api, None
        raise TimeoutError("ADR operation timed out; registration PUT is not retried")

    def register(self, timeout=120):
        started = self.clock()
        family = self.config.get("identityFamily", "X509Dps")
        emit(self, "info", "dps", "provisioning_started", family=family)
        try:
            result = self._register(timeout)
            emit(self, "info", "dps", "assignment", host=result[0], deviceId=result[1])
            return result
        except Exception as exc:
            reason = "registration_disabled" if isinstance(exc, PermissionError) else "timeout" if isinstance(exc, TimeoutError) else "provisioning_failed"
            emit(self, "info", "dps", "registration_skipped" if isinstance(exc, PermissionError) else "registration_failed", reason=reason)
            raise
        finally:
            emit(self, "debug", "dps", "provisioning_result", latencyMs=round((self.clock() - started) * 1000, 3))

    def _register(self, timeout=120):
        family = self.config.get("identityFamily", "X509Dps")
        if family == "X509DpsAdr":
            return self._register_adr(timeout)
        if family != "X509Dps":
            raise ValueError("Unsupported provisioning family: " + str(family))
        if self.state.get("assignedHub") and self.state.get("deviceId"):
            emit(self, "info", "dps", "cached_assignment", host=self.state["assignedHub"], deviceId=self.state["deviceId"], registration="skipped")
            return self.state["assignedHub"], self.state["deviceId"]
        if not self.config.get("allowDpsRegistration", False):
            raise PermissionError("DPS registration requires explicit permission")
        old_message = self.transport.on_message
        old_connect = self.transport.on_connect
        self.transport.on_message = self._message
        self.transport.on_connect = lambda: self.transport.subscribe("$dps/registrations/res/#")
        registration = self.config["registrationId"]
        username = self.config["idScope"] + "/registrations/" + registration + "/api-version=2019-03-31"
        deadline = self.clock() + timeout
        operation = None
        next_request = self.clock()
        waiting = False
        try:
            self.transport.connect(self.config["globalEndpoint"], registration, username)
            while self.clock() < deadline:
                self.transport.poll(0.05)
                if self.responses:
                    topic, body = self.responses.pop(0)
                    status = int(topic.split("/")[3])
                    args = query(topic)
                    if args.get("$rid") != str(self.rid):
                        emit(self, "debug", "dps", "response_ignored", rid=args.get("$rid"), reason="rid_mismatch", status=status)
                        continue
                    waiting = False
                    emit(self, "debug", "dps", "ACK", rid=args.get("$rid"), status=status)
                    emit(self, "trace", "dps", "ACK", rid=args.get("$rid"), status=status)
                    retry = max(1, min(60, int(args.get("retry-after", "2"))))
                    emit(self, "debug", "dps", "poll_decision", rid=args.get("$rid"), status=status, delaySec=retry)
                    if status == 429 or status >= 500:
                        next_request = self.clock() + retry
                    elif status not in [200, 202]:
                        raise ConnectionError("DPS registration rejected: " + str(status))
                    elif body.get("status") == "assigned":
                        result = body.get("registrationState", body)
                        hub = result.get("assignedHub")
                        device = result.get("deviceId")
                        if not hub or not device:
                            raise ValueError("Incomplete DPS assignment")
                        self.state["assignedHub"] = hub
                        self.state["deviceId"] = device
                        return hub, device
                    elif body.get("status") == "failed":
                        raise ConnectionError("DPS assignment failed")
                    else:
                        operation = body.get("operationId", operation)
                        next_request = self.clock() + retry
                if self.transport.connected and not waiting and self.clock() >= next_request:
                    self.rid += 1
                    topic = "$dps/registrations/PUT/iotdps-register/?$rid=" + str(self.rid)
                    payload = {"registrationId": registration}
                    if operation:
                        topic = "$dps/registrations/GET/iotdps-get-operationstatus/?$rid=" + str(self.rid) + "&operationId=" + urllib.parse.quote(operation, safe="")
                        payload = {}
                    self.transport.publish(topic, json.dumps(payload), qos=1)
                    waiting = True
                self.sleep(0.01)
            raise TimeoutError("DPS registration timed out")
        finally:
            self.transport.disconnect()
            self.transport.on_message = old_message
            self.transport.on_connect = old_connect
