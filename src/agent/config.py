# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import json
import os
from log import level_from, directory_from, emit


def load_config(path):
    with open(path, encoding="utf-8-sig") as f:
        config = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for name in ["cert", "key", "stateFile", "payloadDir"]:
        if name not in config:
            raise ValueError("Missing config field: " + name)
        if not os.path.isabs(config[name]):
            config[name] = os.path.join(base, config[name])
    if config.get("payloadPublicKeyPem") and not os.path.isabs(config["payloadPublicKeyPem"]):
        config["payloadPublicKeyPem"] = os.path.join(base, config["payloadPublicKeyPem"])
    for name in ["registrationId", "idScope", "globalEndpoint"]:
        if not config.get(name) or "<" in config[name]:
            raise ValueError("Missing real config field: " + name)
    family = config.get("identityFamily", "X509Dps")
    if family not in ["X509Dps", "X509DpsAdr"]:
        raise ValueError("Unsupported provisioning family: " + str(family))
    config["identityFamily"] = family
    if family == "X509DpsAdr":
        for name in ["bootstrapCert", "bootstrapKey", "deviceKey", "csr"]:
            if not config.get(name):
                raise ValueError("Missing ADR config field: " + name)
            if not os.path.isabs(config[name]):
                config[name] = os.path.join(base, config[name])
        if config.get("certificateExpiryFile") and not os.path.isabs(config["certificateExpiryFile"]):
            config["certificateExpiryFile"] = os.path.join(base, config["certificateExpiryFile"])
        margin = config.get("adrRenewBeforeSeconds", 7 * 86400)
        if not isinstance(margin, (int, float)) or margin < 0:
            raise ValueError("adrRenewBeforeSeconds must be nonnegative")
        renewal = config.get("adrRenewAfterSeconds", 60 * 86400)
        if not isinstance(renewal, (int, float)) or renewal <= 0:
            raise ValueError("adrRenewAfterSeconds must be positive")
    config.setdefault("logLevel", "info")
    level_from(config)  # Reject before starting workers or provisioning.
    directory_from(config)
    config["configPath"] = os.path.abspath(path)
    config.setdefault("allowDpsRegistration", False)
    return config


class StateStore:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict):
                raise ValueError("State must be an object")

    def save(self):
        if getattr(self, "protected_io", False):
            from atomic_io import protected_write
            protected_write(self.path, json.dumps(self.data).encode("utf-8"))
            emit(self, "debug", "state", "saved")
            return
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        temporary = self.path + ".new"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(temporary, self.path)
        emit(self, "debug", "state", "saved")
