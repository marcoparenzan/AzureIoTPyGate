# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
def on_start(ctx):
    ctx.state["badTicks"] = 0

def on_tick(ctx):
    ctx.state["badTicks"] += 1
    if ctx.state["badTicks"] == 3:
        raise RuntimeError("fixture error must not leak")
    ctx.send_telemetry({"ts": ctx.now(), "deviceId": ctx.device_id, "payloadVersion": ctx.payload_version, "temperature": 22}, {"schema": "thermostat.v4"})
