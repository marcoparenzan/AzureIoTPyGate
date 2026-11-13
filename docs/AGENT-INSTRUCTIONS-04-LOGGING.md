# Round 4 — diagnostic logging with a level flag

Audience: a coding agent working in this repository. Read `AGENTS.md` first; its rules win over this file. Talk to the owner in Italian; write code and documentation in English.

## Goal

When the owner starts a device (`.\scripts\Start-Device.ps1 device01`, `pysharp run scripts\start_device.py device01`, or the C# launcher) they see almost nothing. Add diagnostic logging, controlled by a flag, so they can see what the device is doing, what it sends and what it receives (telemetry, device twin, direct methods, OTA).

Current state: `src/agent` emits only a few fixed events through `ctx.log` (connected, disconnected, message_error, worker_restarting, worker_recovered, worker_recovery_failed, payload_error). `src/config.example.json` has a `logLevel` key: check whether it is used anywhere before reusing it; do not leave a dead setting.

## Coordination

The security fixes for findings 2 to 5 in `D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\agent-review-2026-11-12.md` touch the same files (`runtime.py`, `ota.py`, `main.py`). Check `ROADMAP.md` for a "Round 3 execution evidence" entry saying they are done. If they are in progress, stop and tell the owner. Do not overwrite their edits; keep your changes in separate hunks and report any conflict.

## Requirements

1. **Levels.** `off < info < debug < trace`, default `info`. Source, in order of precedence: launcher switch `-LogLevel` (Start-Device.ps1; same option in `StartDevice.cs` and `start_device.py`, passed to the agent as environment variable `PYGATE_LOG_LEVEL`), then config key `logLevel`, then default. Reject unknown values with a clear message. Do not change the existing guard logic of the launchers; keep the three launchers equivalent.
2. **info** — lifecycle only: startup (device id, config file path, never contents), DPS result (cached assignment used or registration skipped, assigned hub host), MQTT connect/disconnect with reason code, OTA lifecycle (manifest received, size/hash/signature check result, activation, probation start/end, rollback and reason), worker restarts, watchdog and heartbeat timeouts, state saves.
3. **debug** — adds one summary line per message in both directions (direction, topic, size, key names, status), callback timings, retry/backoff decisions.
4. **trace** — adds the full content of every message in both directions:
   - OUT telemetry: full JSON body as published, topic, QoS, message id, size, and the PUBACK with latency in ms.
   - OUT reported twin patch: full JSON, topic, `$rid`, and the response status with the same `$rid`.
   - OUT direct method response: status and full JSON body.
   - IN desired twin: full document or patch (`$version` included), topic, size, and a line listing which keys changed compared with the last desired seen.
   - IN direct method request: method name, `$rid`, full JSON body.
   - IN twin GET response and any cloud-to-device message the agent handles.
5. **Line format.** One line per event: ISO-8601 UTC timestamp built with `datetime.utcnow().isoformat() + "Z"` (PySharp gap: do not rely on `now(timezone.utc)`), level, component, event, `key=value` pairs. Example:
   ```
   2026-11-12T20:41:03.512Z TRACE mqtt OUT telemetry topic=devices/device01/messages/events/ qos=1 mid=17 size=96 body={"ts":"...","deviceId":"device01","temperature":21.4}
   2026-11-12T20:41:03.540Z TRACE mqtt ACK mid=17 latencyMs=28
   2026-11-12T20:41:10.001Z TRACE twin IN desired $version=5 changed=setpoint body={"setpoint":22.0,"$version":5}
   2026-11-12T20:41:10.050Z TRACE twin OUT reported rid=9 body={"setpoint":22.0}
   2026-11-12T20:41:10.090Z TRACE twin ACK rid=9 status=204
   ```
6. **Destination.** Console (the launchers must keep showing the agent's console output) and a rotating file under configurable `logDir` (default: next to the state file, never inside this repository). Cap file size and file count so logs cannot grow without bound. Handle Windows sharing violations; check `os.replace`/`os.rename` behaviour in PySharp before relying on it for rotation.
7. **Redaction** applies recursively to every body, before anything is written, on console and file, at every level:
   - Value becomes `"<redacted>"` for any key whose lower-case name contains: key, secret, token, password, pwd, sas, sig, signature, cert, pem, connectionstring, privatekey.
   - Any URL-looking string keeps scheme, host and path; the query string and fragment are dropped (`https://host/path?<redacted>`).
   - OTA manifest fields name, version, size, sha256 stay visible; the signature is redacted (log only its length).
   - Single strings over 256 characters and bodies over 4096 characters are truncated with a marker `...<truncated N chars>`.
   - Never log private keys, certificate or PEM contents, SAS tokens, connection strings or raw exception text.
8. **Implementation.** One small module (for example `src/agent/log.py`). Keep the `ctx.log` API working and route it through the new logger so old event names survive. The worker process (`payload_worker.py`) logs through the existing redacted stream channel, not directly to the file. Logging must never raise into the agent and never block callbacks: wrap file errors, fall back to console, no network and no long I/O. Respect the limits in ROADMAP "PySharp gaps" (no `PYTHONPATH`, no `__file__` in entry scripts).
9. **Trace warning.** Trace is for local diagnosis. State in `USAGE.md` that trace output may contain device data and must stay outside the repository. At debug or trace level the launcher prints one startup line naming the log file.

## Tests

Add tests in `tests/python` and register them in the xUnit wrapper in `tests/AzureIoTPyGate.Tests`, using GUID scratch directories under `D:\Dev\BuildArtifacts\AzureIoTPyGate\tmp`:
- level filtering (off, info, debug, trace) and precedence of env over config; invalid level rejected;
- rotation caps are respected;
- a logging failure does not stop the agent;
- sentinel secrets inside nested desired properties (`sasToken`, `downloadUrl` with `?sig=SENTINEL`, `privateKey`, a very long string) never appear in console or file output at trace level, while the telemetry body and non-secret desired values do appear;
- the trace line for a published telemetry message contains the same JSON passed to the transport; the line for an incoming desired patch contains `$version` and the changed keys.

## Documentation

Update `USAGE.md` (how to turn logging on, per launcher, where files go, a few example lines), `ARCHITECTURE.md` (logger component, redaction rules), `ROADMAP.md` (what changed, evidence labels) and `src/config.example.json` (`logLevel`, `logDir`).

## Do not change

Wire formats, telemetry payload content, OTA/manifest/signature logic, PySharp, sibling repositories.

## Verification

`dotnet build src/AzureIoTPyGate.slnx` (0 warnings, 0 errors). Check with `Get-Process dotnet,pysharp,python` that nobody else is testing or running devices, then run `dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak"` once, serially. Do not start a real device, do not run soak, do not register with DPS. A live run of `device01` is for the owner to authorize; until then live output is not-verified.

## Rules that override everything

No commit, publish, global tool install/update, Azure change or DPS registration; never enable `allowDpsRegistration`; never store or print credentials, keys, SAS tokens or connection strings; all logs and scratch go to `D:\Dev\BuildArtifacts\AzureIoTPyGate`; move anything you remove to `D:\Dev\BuildArtifacts\trash` and tell the owner; do not stop processes you did not start. Report each result as verified-live, verified-offline or not-verified.

## Final report (Italian)

Changed files, test counts with evidence, a sample of the new log lines produced by the tests, what remains not-verified.
