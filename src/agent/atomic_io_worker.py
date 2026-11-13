# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# Filesystem exceptions not translated by PySharp are contained in this process.
import base64
import json
import os
import sys
request = json.loads(input())
path = request["path"]
os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path + ".new", "wb") as f:
    f.write(base64.b64decode(request["data"]))
os.replace(path + ".new", path)
