# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import base64
import json
import os
import subprocess


def protected_write(path, data):
    command = ["pysharp", "run", os.path.join(os.path.dirname(os.path.abspath(__file__)), "atomic_io_worker.py")]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    try:
        process.communicate(json.dumps({"path": os.path.abspath(path), "data": base64.b64encode(data).decode("ascii")}), timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise OSError("atomic_write_timeout")
    if process.returncode != 0:
        raise OSError("atomic_write_failed")
