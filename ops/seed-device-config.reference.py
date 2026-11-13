"""Seed agent config, cached assignment state and expiry metadata for devices registered by Shipyard.
Writes only inside D:\\Configurations\\AzureIoTForge\\devices\\<id>. No Azure call, no key is read or printed."""
import datetime
import hashlib
import json
import os
import subprocess

ROOT = r"D:\Configurations\AzureIoTForge\devices"
TEMPLATE = os.path.join(ROOT, "<probe-device-01>", "final-agent-config.json")


def openssl_date(pem, flag):
    out = subprocess.check_output(["openssl", "x509", "-in", pem, "-noout", flag], text=True).strip()
    return datetime.datetime.strptime(out.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)


template = json.load(open(TEMPLATE, encoding="utf-8-sig"))

for device in ["device01", "device02", "device03"]:
    folder = os.path.join(ROOT, device)
    chain = os.path.join(folder, "issued-chain.pem")
    registration = json.load(open(os.path.join(folder, "dps-registration.json"), encoding="utf-8-sig"))["registrationState"]
    assert registration["deviceId"] == device and registration["status"] == "assigned"

    leaf = os.path.join(folder, "issued-chain-01.pem")
    not_after = openssl_date(leaf, "-enddate")
    not_before = openssl_date(leaf, "-startdate")
    digest = hashlib.sha256(open(chain, "rb").read()).hexdigest()

    expiry_path = os.path.join(folder, "issued-chain.expiry.json")
    json.dump({"notAfter": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"), "certificateSha256": digest},
              open(expiry_path, "w", encoding="utf-8", newline=""), separators=(",", ":"))

    state = {
        "assignedHub": registration["assignedHub"],
        "deviceId": device,
        "issuedCert": chain,
        "adrIssuedAt": not_before.timestamp(),
    }
    state_path = os.path.join(folder, "agent-state.json")
    json.dump(state, open(state_path, "w", encoding="utf-8", newline=""), indent=2)

    config = dict(template)
    config.update({
        "registrationId": device,
        "stateFile": state_path,
        "csr": os.path.join(folder, "device.csr.pem"),
        "bootstrapKey": os.path.join(folder, "bootstrap-private-key.pem"),
        "bootstrapCert": os.path.join(folder, "bootstrap-chain.pem"),
        "key": os.path.join(folder, "bootstrap-private-key.pem"),
        "cert": os.path.join(folder, "bootstrap-chain.pem"),
        "deviceKey": os.path.join(folder, "device-private-key.pem"),
        "otaDir": os.path.join(folder, "ota"),
        "certificateExpiryFile": expiry_path,
        "allowDpsRegistration": False,
        "allowReprovision": False,
    })
    config_path = os.path.join(folder, "agent-config.json")
    json.dump(config, open(config_path, "w", encoding="utf-8", newline=""), indent=2)
    print(device, "ok | hub", registration["assignedHub"], "| notAfter", expiry_path and not_after.strftime("%Y-%m-%d"), "| files:",
          ", ".join(sorted(["agent-config.json", "agent-state.json", "issued-chain.expiry.json"])))
