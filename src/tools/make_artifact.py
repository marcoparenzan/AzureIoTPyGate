# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import hashlib
import json
import os
import sys
import pysharp_signing

ALGORITHM = "ecdsa-p384-sha384"


def make_manifest(path, name, version, url, private_pem):
    with open(path, "rb") as f:
        data = f.read()
    signature = pysharp_signing.sign(ALGORITHM, private_pem, data)
    return {"name": name, "version": version, "url": url, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "sig": signature.hex()}


def main():
    if len(sys.argv) != 7:
        print("Usage: make_artifact.py script name version https-url external-private-key.pem external-manifest.json")
        return 2
    with open(sys.argv[5], encoding="utf-8") as f:
        key = f.read()
    manifest = make_manifest(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], key)
    with open(sys.argv[6], "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    # Never print signed URLs, keys or manifest contents.
    print("ARTIFACT_MANIFEST_WRITTEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
