# Copyright (c) 2026 Marco Parenzan
# Licensed under the MIT License. See LICENSE in the project root.
import random

VERSION_INFO = {"name": "thermostat", "version": 1}


def alarm_transition(state, temperature, thresholds):
    high = float(thresholds.get("high", 26))
    low = float(thresholds.get("low", 16))
    hysteresis = float(thresholds.get("hysteresis", 0.5))
    if temperature > high:
        return "high"
    if temperature < low:
        return "low"
    if state == "high" and temperature >= high - hysteresis:
        return "high"
    if state == "low" and temperature <= low + hysteresis:
        return "low"
    return "normal"


def on_start(ctx):
    ctx.state.setdefault("temperature", 21.0)
    ctx.state.setdefault("ambient", 18.0)
    ctx.state.setdefault("alarmState", "normal")
    ctx.state.setdefault("ticks", 0)
    ctx.log("payload_started")


def on_desired(ctx, patch):
    desired = ctx.desired
    ctx.report({"applied": {k: desired[k] for k in ["targetTemperature", "thresholds", "telemetryIntervalSec"] if k in desired}})


def on_tick(ctx):
    desired = ctx.desired
    target = float(desired.get("targetTemperature", 21))
    thresholds = desired.get("thresholds", {"high": 26, "low": 16, "hysteresis": 0.5})
    ctx.state["ticks"] += 1
    ambient = ctx.state["ambient"] + random.uniform(-0.02, 0.02)
    ctx.state["ambient"] = ambient
    temperature = ctx.state["temperature"]
    temperature += 0.08 * (target - temperature) + 0.005 * (ambient - temperature) + random.uniform(-0.05, 0.05)
    if ctx.state.get("injectFault") and ctx.state["ticks"] % 30 == 0:
        temperature = float(thresholds["high"]) + 2
    ctx.state["temperature"] = temperature
    previous = ctx.state["alarmState"]
    alarm = alarm_transition(previous, temperature, thresholds)
    ctx.state["alarmState"] = alarm
    body = {"ts": ctx.now(), "deviceId": ctx.device_id, "temperature": round(temperature, 3), "setpoint": target, "payloadVersion": ctx.payload_version}
    props = {"schema": "thermostat.v1", "alarmState": alarm}
    if previous != alarm:
        ctx.report({"alarmState": alarm})
        if alarm != "normal":
            props["alarm"] = alarm
            body["alarm"] = {"type": alarm, "threshold": thresholds[alarm], "value": round(temperature, 3)}
    ctx.report({"currentTemperature": round(temperature, 3)})
    ctx.send_telemetry(body, props)


def on_stop(ctx):
    ctx.log("payload_stopped")
