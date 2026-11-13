# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
# v2: faster response; temperature remains Celsius, humidity is percent RH.
VERSION_INFO = {"name": "thermostat", "version": 2, "units": "Celsius; humidity %RH"}

def on_start(ctx):
    ctx.state["otaTicks"] = 0

def on_tick(ctx):
    ctx.state["otaTicks"] += 1
    target = float(ctx.desired.get("targetTemperature", 21))
    temperature = float(ctx.state.get("temperature", 21))
    temperature += (target - temperature) * 0.2
    ctx.state["temperature"] = temperature
    ctx.send_telemetry({"ts": ctx.now(), "deviceId": ctx.device_id, "temperature": temperature, "setpoint": target, "payloadVersion": ctx.payload_version, "humidity": 40 + ctx.state["otaTicks"] % 5}, {"schema": "thermostat.v2"})

def on_desired(ctx, patch):
    pass

def on_stop(ctx):
    pass
