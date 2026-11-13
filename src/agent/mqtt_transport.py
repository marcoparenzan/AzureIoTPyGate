# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import random
import time
import urllib.parse
from log import emit, message


def merge(target, patch):
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            merge(target[key], value)
        else:
            target[key] = value
    return target


def query(topic):
    return dict(urllib.parse.parse_qsl(topic.split("?", 1)[1])) if "?" in topic else {}


def backoff(attempt, jitter=random.random):
    return min(60.0, min(30.0, 2.0 ** min(attempt, 5)) * (1.0 + jitter()))


class Transport:
    def log_message(self, direction, topic, payload, **fields):
        # Diagnostic parsing must never affect transport delivery or validation.
        try:
            args = query(topic)
            if "$rid" in args:
                fields["rid"] = args["$rid"]
            if "$version" in args:
                fields["version"] = args["$version"]
            if topic.startswith(("$iothub/twin/res/", "$iothub/methods/res/", "$dps/registrations/res/")):
                fields["status"] = topic.split("/")[3]
            if topic.startswith("$iothub/methods/POST/"):
                fields["name"] = urllib.parse.unquote(topic.split("/")[3])
            message(self, direction, topic, payload, **fields)
        except Exception:
            pass

    def log_ack(self, mid, topic, latency, status):
        fields = {"mid": mid, "topic": topic, "latencyMs": latency, "status": status}
        emit(self, "debug", "mqtt", "ACK", **fields)
        emit(self, "trace", "mqtt", "ACK", **fields)

    def connect(self, host, device_id, username=None):
        raise NotImplementedError()
    def publish(self, topic, payload, qos=0):
        raise NotImplementedError()
    def subscribe(self, topic):
        raise NotImplementedError()
    def poll(self, timeout=0.1):
        raise NotImplementedError()
    def disconnect(self):
        raise NotImplementedError()


class MqttTransport(Transport):
    def __init__(self, cert, key, clock=time.monotonic, jitter=random.random):
        import ssl
        import paho.mqtt.client as mqtt
        self.clock = clock
        self.jitter = jitter
        self.mqtt = mqtt
        self.client = None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        context.load_cert_chain(cert, key)
        self.context = context
        self.connected = False
        self.stopped = False
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None
        self.attempt = 0
        self.next_retry = 0
        self.endpoint = None
        self.metrics = {"connectAttempts": 0, "connections": 0, "unexpectedDisconnects": 0, "networkErrors": 0, "telemetrySent": 0, "telemetryPubacks": 0, "publishFailures": 0}
        self.pending_publications = {}
        self.publication_times = {}

    def set_certificate(self, cert, key):
        import ssl
        context = ssl.create_default_context()
        context.load_cert_chain(cert, key)
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("MQTT requires verified TLS")
        self.context = context

    def connect(self, host, device_id, username=None):
        # Use the public constructor when switching identities from DPS to Hub.
        if self.client is not None:
            self.client.disconnect()
        self.client = self.mqtt.Client(self.mqtt.CallbackAPIVersion.VERSION2, client_id=device_id, protocol=self.mqtt.MQTTv311)
        self.client.tls_set_context(self.context)
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.on_message = self._message
        self.client.on_publish = self._published
        self.client.username_pw_set(username)
        self.metrics["connectAttempts"] += 1
        self.endpoint = host
        self.stopped = False
        emit(self, "info", "mqtt", "connecting", host=host, deviceId=device_id, attempt=self.metrics["connectAttempts"])
        try:
            self.client.connect(host, 8883, 60)
            self.next_retry = self.clock() + 10
        except Exception:
            self._schedule("network")

    def _schedule(self, reason):
        self.metrics["networkErrors"] += 1
        self.connected = False
        delay = backoff(self.attempt, self.jitter)
        self.next_retry = self.clock() + delay
        self.attempt += 1
        emit(self, "info", "mqtt", "disconnected", host=self.endpoint, reason=reason)
        emit(self, "debug", "mqtt", "retry_scheduled", attempt=self.attempt, delaySec=delay, reason=reason)
        if self.on_disconnect:
            self.on_disconnect(reason)

    def _connected(self, client, userdata, flags, reason_code, properties):
        emit(self, "info", "mqtt", "CONNACK", host=self.endpoint, reasonCode=reason_code.value)
        if reason_code.is_failure:
            reason = "unauthorized" if reason_code.value in [134, 135] else "refused"
            self._schedule(reason)
            return
        self.connected = True
        self.metrics["connections"] += 1
        self.attempt = 0
        emit(self, "info", "mqtt", "connected", host=self.endpoint, reasonCode=reason_code.value)
        if self.on_connect:
            self.on_connect()

    def _disconnected(self, client, userdata, flags, reason_code, properties):
        emit(self, "info", "mqtt", "DISCONNECT", host=self.endpoint, reasonCode=reason_code.value, reason="requested" if self.stopped else "network")
        if not self.stopped:
            self.metrics["unexpectedDisconnects"] += 1
            self._schedule("network")

    def _message(self, client, userdata, msg):
        started = self.clock()
        self.log_message("IN", msg.topic, msg.payload, qos=getattr(msg, "qos", None), mid=getattr(msg, "mid", None))
        try:
            if self.on_message:
                self.on_message(msg.topic, msg.payload.decode("utf-8"))
        finally:
            emit(self, "debug", "mqtt", "message_callback", topic=msg.topic, latencyMs=round((self.clock() - started) * 1000, 3))

    def publish(self, topic, payload, qos=0):
        if not self.connected:
            raise ConnectionError("Transport offline")
        started = self.clock()
        result = self.client.publish(topic, payload, qos=qos)
        # PySharp IntEnum __ne__ may disagree with __eq__; normalize to primitive.
        if getattr(result.rc, "value", result.rc) != 0:
            self.metrics["publishFailures"] += 1
            emit(self, "debug", "mqtt", "publish_rejected", topic=topic, qos=qos, status=getattr(result.rc, "value", result.rc))
            raise ConnectionError("MQTT publish rejected")
        if topic.startswith("devices/"):
            self.metrics["telemetrySent"] += 1
        if qos == 1:
            self.pending_publications[result.mid] = topic
            self.publication_times[result.mid] = started
        self.log_message("OUT", topic, payload, qos=qos, mid=result.mid, status=getattr(result.rc, "value", result.rc), latencyMs=round((self.clock() - started) * 1000, 3))

    def _published(self, client, userdata, mid, reason_code, properties):
        topic = self.pending_publications.pop(mid, None)
        sent = self.publication_times.pop(mid, None)
        self.log_ack(mid, topic, round((self.clock() - sent) * 1000, 3) if sent is not None else None, getattr(reason_code, "value", None))
        if reason_code.is_failure:
            self.metrics["publishFailures"] += 1
        elif topic and topic.startswith("devices/"):
            self.metrics["telemetryPubacks"] += 1

    def drain(self, timeout=5):
        deadline = self.clock() + timeout
        while self.pending_publications and self.connected and self.clock() < deadline:
            self.poll(0.1)
        return not self.pending_publications

    def subscribe(self, topic):
        result, mid = self.client.subscribe(topic, qos=1)
        emit(self, "debug", "mqtt", "OUT subscribe", topic=topic, qos=1, mid=mid, status=getattr(result, "value", result))

    def poll(self, timeout=0.1):
        if self.stopped:
            return
        if not self.connected and self.endpoint and self.clock() >= self.next_retry:
            try:
                emit(self, "info", "mqtt", "connecting", host=self.endpoint, reason="retry", attempt=self.attempt)
                self.client.reconnect()
                self.next_retry = self.clock() + 10
                emit(self, "debug", "mqtt", "connect_wait", delaySec=10)
            except Exception:
                self._schedule("network")
        try:
            self.client.loop(timeout=timeout)
        except Exception:
            self._schedule("network")

    def disconnect(self):
        emit(self, "info", "mqtt", "disconnecting", host=self.endpoint, reason="requested")
        self.stopped = True
        if self.client is not None:
            self.client.disconnect()
        self.connected = False


class LoopbackTransport(Transport):
    def __init__(self, twin_desired=None):
        self.connected = False
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None
        self.desired = twin_desired or {"$version": 1}
        self.reported = {}
        self.published = []
        self.subscriptions = []
        self.inbox = []
        self.dps_responses = []
        self.adr_responses = []
        self.adr_requests = []
        self.certificate = None
        self.device_id = "loopback"
        self.endpoint = None
        self.mid = 0

    def set_certificate(self, cert, key):
        self.certificate = (cert, key)

    def dps_https_request(self, method, path, body):
        self.adr_requests.append((method, path, body))
        if not self.adr_responses:
            raise ConnectionError("No loopback ADR response")
        status, response, retry = self.adr_responses.pop(0)
        raw = response if isinstance(response, bytes) else json.dumps(response).encode("utf-8")
        return status, raw, retry

    def connect(self, host, device_id, username=None):
        self.endpoint = host
        emit(self, "info", "mqtt", "connecting", host=host, deviceId=device_id)
        self.connected = True
        self.device_id = device_id
        emit(self, "info", "mqtt", "connected", host=host, reasonCode=0)
        if self.on_connect:
            self.on_connect()

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0):
        if not self.connected:
            raise ConnectionError("Transport offline")
        self.published.append((topic, payload))
        self.mid += 1
        self.log_message("OUT", topic, payload, qos=qos, mid=self.mid, status=0)
        if qos == 1:
            self.log_ack(self.mid, topic, 0, "loopback_accepted")
        rid = query(topic).get("$rid", "0")
        if topic.startswith("$iothub/twin/GET/"):
            self.inject("$iothub/twin/res/200/?$rid=" + rid, {"desired": self.desired, "reported": self.reported})
        elif topic.startswith("$iothub/twin/PATCH/properties/reported/"):
            merge(self.reported, json.loads(payload))
            self.inject("$iothub/twin/res/204/?$rid=" + rid, {})
        elif topic.startswith("$dps/") and self.dps_responses:
            response = self.dps_responses.pop(0)
            self.inject(response[0].replace("RID", rid), response[1])

    def inject(self, topic, payload):
        self.inbox.append((topic, json.dumps(payload) if isinstance(payload, dict) else payload))

    def inject_desired(self, patch):
        self.inject("$iothub/twin/PATCH/properties/desired/?$version=" + str(patch.get("$version", 0)), patch)

    def inject_method(self, name, rid="1", payload=None):
        self.inject("$iothub/methods/POST/" + name + "/?$rid=" + rid, payload or {})

    def poll(self, timeout=0.1):
        if self.connected and self.inbox:
            topic, payload = self.inbox.pop(0)
            started = time.monotonic()
            self.log_message("IN", topic, payload)
            try:
                if self.on_message:
                    self.on_message(topic, payload)
            finally:
                emit(self, "debug", "mqtt", "message_callback", topic=topic, latencyMs=round((time.monotonic() - started) * 1000, 3))

    def disconnect(self):
        emit(self, "info", "mqtt", "disconnected", host=self.endpoint, reason="requested", reasonCode=0)
        self.connected = False

    def force_disconnect(self, reason="network"):
        emit(self, "info", "mqtt", "disconnected", host=self.endpoint, reason=reason)
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect(reason)
