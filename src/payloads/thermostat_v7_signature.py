# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# Valid source/hash; tests deliberately corrupt the signature.
def on_tick(ctx):
    ctx.send_telemetry({"ts": ctx.now(), "deviceId": ctx.device_id, "payloadVersion": ctx.payload_version, "temperature": 24}, {})
