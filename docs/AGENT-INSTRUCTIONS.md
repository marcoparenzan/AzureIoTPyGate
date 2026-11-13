# AzureIoTPyGate — bootstrap instructions for a coding agent

> # ✅ EXECUTED on 2026-11-12 by the owner's decision, BEFORE the global review. The review is still open.
>
> The owner launched this task without waiting for the review of who owns credentials (Shipyard also creates credentials), so the proposals (devices folder stays, paho folder stays, PyGate writes `agent-config.json`, Forge writes the certificates, project `AzureIoTForge.Credentials`, one repository per gateway, Forge owns `DEVICE-CONTRACT.md`) were applied as defaults. **They are still proposals**: see `D:\Dev\2026\IoTHub\GLOBAL-REVIEW-credentials-and-gateways.md`. Do not run this task again. The originals in AzureIoTForge (`device\`, the four launchers, `AzureIoTForge.DeviceAgent.Tests`) are still in place: task T10 has NOT been done and waits for the owner.


Written for: an AI coding agent that will turn this folder into a self-standing project. The owner (Marco) chats in **Italian**; code, comments, commit messages and documents are in **English**. Use they/them for people whose pronouns are not stated.

This file is the whole task. Read it fully, then read the files named in section 2. Do the tasks in order (section 4), one at a time, and finish each (build green, tests green) before the next.

## 1. What this project is

**AzureIoTPyGate** is the first of a family of *gateways*: a project, and a repository, for each kind of device that can be managed by **AzureIoTForge** (an IoT-Central-like fleet manager for Azure IoT Hub). PyGate is a **simulated device written in Python and run by PySharp** (the owner's .NET Python interpreter, `D:\Dev\Science\PySharp`): a small stable *agent* (DPS registration over HTTPS or MQTT, MQTT over TLS to the hub, device twin, direct methods, signed over-the-air update of the payload) plus a hot-swappable *payload script* that decides what is measured and sent.

Related projects, all under `D:\Dev\2026\IoTHub\` today (**the owner will later move all of them under one new parent folder to make coordinated cross-project changes: never hard-code a path to a sibling project; use a path relative to this repository, with an environment variable to override it**):

| Project | Role | Relation to PyGate |
|---|---|---|
| `AzureIoTForge` | fleet manager (Blazor Server). Knows IoT Hub in its flavours only; it does **not** start devices any more | owns the *device contract* (what a device must do to be managed). PyGate implements it |
| `AzureIoTShipyard` | creates the Azure side (hub, DPS, ADR, certificates) | produces the credential material a device starts from |
| `PySharp` (`D:\Dev\Science\PySharp`) | the interpreter that runs the agent | PyGate is its main user. Rules in section 6 |

Until now this code lived inside AzureIoTForge, under `device\`. It is being moved here because Forge must not know how any device is launched.

## 2. What already exists (done by the previous session, do not redo)

The folder `D:\Dev\2026\IoTHub\AzureIoTPyGate` was created and the relevant files were **copied, not moved**. The originals are still in AzureIoTForge and keep working there; another session removes them later, after you report that this project is green. **Do not touch AzureIoTForge.**

| Copied from AzureIoTForge | To |
|---|---|
| `device\agent\*.py` (12 files) | `src\agent\` |
| `device\payloads\*.py` (8 payloads: v1 good, v2 good, v3 syntax error, v4 runtime error, v5 hang, v6 wrong hash, v7 bad signature, v8 wrong key) | `src\payloads\` |
| `device\tools\make_artifact.py` | `src\tools\` |
| `device\config.example.json` | `src\config.example.json` |
| `device\tests\*.py` (`run_tests.py`, `ota_cases.py`, `adr_cases.py`) | `tests\python\` |
| `src\AzureIoTForge.DeviceAgent.Tests\` (xUnit project that launches PySharp on the Python tests: 30 tests, one is a 10-minute `Soak`) | `tests\AzureIoTPyGate.Tests\` |
| `scripts\Start-Device.ps1`, `start-device.cmd`, `StartDevice.cs`, `start_device.py` (four equivalent launchers) | `scripts\` |
| `LICENSE` (MIT, Copyright (c) 2026 Marco Parenzan) | `LICENSE` |
| a reference seeding script (not part of Forge) | `ops\seed-device-config.reference.py` |

**The copies are not adapted: paths inside them still point into AzureIoTForge.** That is task T2. Everything else (README, AGENTS.md, USAGE.md, ROADMAP.md, OPENPOINTS.md, `.gitignore`, `Directory.Build.props`, `AzureIoTPyGate.slnx`, ARCHITECTURE) does **not** exist yet.

Read, for background (read-only, in the AzureIoTForge repository): `AGENTS.md`, `docs\ARCHITECTURE.md` (§2 device protocol, §3 twin schema, §4 telemetry schema, §5 direct methods, §6 device runtime and worker IPC, the "Spike results" and "Step 11" sections), `docs\steps\02-device-agent.md`, `03-parquet.md` (part B), `04-ota.md`, `11-identity-modes.md`, `OPENPOINTS.md` (sections "PySharp gaps", "PySharp changes", "Step 04", "Identity modes", "Live evidence"), `USAGE.md`.

## 3. Target layout

```
AzureIoTPyGate\
  README.md  USAGE.md (installation first)  ROADMAP.md  OPENPOINTS.md  AGENTS.md  LICENSE  .gitignore
  Directory.Build.props  Directory.Packages.props  AzureIoTPyGate.slnx
  src\       agent\  payloads\  tools\  config.example.json
  tests\     python\  AzureIoTPyGate.Tests\
  scripts\   Start-Device.ps1  start-device.cmd  StartDevice.cs  start_device.py  Preflight.ps1
  ops\       Install-Dependencies.ps1  Seed-DeviceConfig.ps1 (or .py)  runbooks
  docs\      ARCHITECTURE.md  CONTRACT.md  AGENT-INSTRUCTIONS.md (this file)
```

## 4. Tasks, in order

**T1 — rules and skeleton.** Write `AGENTS.md` for this project (English). Copy the rules that apply from AzureIoTForge's `AGENTS.md`: .NET 11 only (`net11.0`); MIT licence with `Copyright (c) 2026 Marco Parenzan`; no secrets in the repository; **generated output goes to `D:\Dev\BuildArtifacts\AzureIoTPyGate`** (bin/obj through `Directory.Build.props`, logs, scratch, reports), never into the repo; do not publish, do not install global tools without asking; **do not change Azure resources and do not register a device with DPS without the owner's explicit OK for that action** (read-only queries are fine); report faithfully (`verified-live` / `verified-offline` / `not-verified`; never say a UI works if it was not opened); never delete from `BuildArtifacts` outright (move to `D:\Dev\BuildArtifacts\trash` and tell the owner); commits only when the owner asks. Add the PySharp boundary text of section 6. Create `.gitignore` (bin, obj, `*.pem`, `*.pfx`, `*.key`, `state.json`, logs), `Directory.Build.props` (same as Forge's, with the artifacts root `D:\Dev\BuildArtifacts\AzureIoTPyGate\`), `Directory.Packages.props` (exact versions, nothing floating; xunit 2.9.3, Microsoft.NET.Test.Sdk 17.14.1, xunit.runner.visualstudio 3.1.5, coverlet.collector 6.0.4, as in Forge), and `AzureIoTPyGate.slnx` with the test project.

**T2 — remove every dependency on AzureIoTForge from the copies.** Exact places (counts from a grep of the copies):
- the four launchers in `scripts\` locate the agent with `..\device\agent` (`Start-Device.ps1`, `start-device.cmd`, `StartDevice.cs` as `"..", "device", "agent"`, `start_device.py` the same): change to `..\src\agent`. They find the device folders under `FORGE_CONFIG_ROOT` (default `D:\Configurations\AzureIoTForge`): introduce `PYGATE_DEVICES_ROOT` (the devices folder itself), with the fallback chain `PYGATE_DEVICES_ROOT`, then `%FORGE_CONFIG_ROOT%\devices`, then `D:\Configurations\AzureIoTForge\devices`; the last two are legacy and must stay working because the existing device folders live there (`device01`, `device02`, `device03`);
- `tests\AzureIoTPyGate.Tests\UnitTest1.cs` (15 matches) and its `.csproj`: project and namespace `AzureIoTPyGate.Tests`; the repository root variable `FORGE_REPO_ROOT` becomes `PYGATE_ROOT` (default: found by walking up to `AzureIoTPyGate.slnx`); `device\tests\run_tests.py` becomes `tests\python\run_tests.py`; scratch under `D:\Dev\BuildArtifacts\AzureIoTForge\tmp\...` becomes `D:\Dev\BuildArtifacts\AzureIoTPyGate\...`;
- `tests\python\*.py` (11 matches): `FORGE_DEVICE_TEST_SCRATCH` -> `PYGATE_TEST_SCRATCH`, `FORGE_ADR_FIXTURE_ROOT` -> `PYGATE_ADR_FIXTURE_ROOT`, `FORGE_SOAK_AGENT` -> `PYGATE_SOAK_AGENT`, `FORGE_TEST_CERT` / `FORGE_TEST_KEY` -> `PYGATE_TEST_CERT` / `PYGATE_TEST_KEY`; keep the old names working as a fallback for one release and say so in USAGE;
- `src\agent\ota.py`, `worker_runtime.py`: one mention each (read and fix); `FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC` -> `PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC` (with fallback);
- `FORGE_PAHO_SITE` -> `PYGATE_PAHO_SITE` (with fallback). The device configs name the folder with paho in `"pahoSite"`; the current stable folder is `D:\Configurations\AzureIoTForge\python-site`. Keep it working as is; `ops\Install-Dependencies.ps1` (T3) creates one in a place chosen by `PYGATE_PYTHON_SITE`.
Keep behaviour identical: this task is renaming, not redesign. After it, `grep -ri "AzureIoTForge" .` must find only (a) the legacy fallbacks, (b) documentation that names Forge on purpose, (c) the real config folder `D:\Configurations\AzureIoTForge`, each marked `legacy` or `contract`.

**T3 — `ops\`.** (a) `Install-Dependencies.ps1`: creates the stable paho folder (`pysharp install paho-mqtt==2.1.0` into it, or a copy of an existing one; no global install; say what it did). (b) `Seed-DeviceConfig`: turn `ops\seed-device-config.reference.py` into a proper tool: from a **credential package** (a device folder holding `dps-registration.json`, `issued-chain.pem`, the keys, `bootstrap-*.pem`, `device.csr.pem`) it writes `agent-config.json` (with `allowDpsRegistration: false`), `agent-state.json` with the cached assignment, and `issued-chain.expiry.json` (`{"notAfter":"...Z","certificateSha256":"<sha256 of issued-chain.pem>"}`) when missing. It never contacts Azure. Remove the hard-coded device list from the reference. (c) a short runbook per operation in `ops\` (start a device, stop it, rotate the paho site, recover from a leaked worker process).

**T4 — tests green in the new place.** Run one project at a time. `dotnet test tests\AzureIoTPyGate.Tests --filter "Category!=Soak"` (expect 30 tests minus the soak, all passing) and the Python runner directly with the installed `pysharp` (global tool is `2.5.1-net11-preview.10`). Then the 10-minute soak **once, alone** (`--filter "Category=Soak"`; three concurrent loopback agents, zero restarts, zero payload errors), with nothing else running. After any test run, `Get-Process pysharp` must show nothing left over: the agent must not leak worker processes (this happened before). Do not start the soak while another session runs tests or devices.

**T5 — the launchers.** Check all four with `-DryRun`/`--dry-run` and with the guard cases (bad id, missing config, config without `allowDpsRegistration: false`, a device already running). A real 12-second start is allowed **only with the owner's explicit OK for that moment** (it sends telemetry to the hub of the `<hub>` environment using the cached assignment, no registration, no change in Azure; and IoT Hub allows one connection per device id, so check that the device is not already running). Add `scripts\Preflight.ps1` (read-only): .NET 11 SDK, `pysharp` on PATH and its version, the paho folder, the devices root, each device config readable and `allowDpsRegistration: false`, no stray `pysharp` processes. It prints a table and exits non-zero on a blocker; it performs no login and no Azure call.

**T6 — documentation (English).**
- `README.md`: what a gateway is, what PyGate does, links.
- `USAGE.md`: **installation first** (prerequisites, `ops\Install-Dependencies.ps1`, where device folders live), then how to start a device in the four ways, how to run the tests, the 10-minute soak and its filter, troubleshooting.
- `docs\ARCHITECTURE.md`: move here, from AzureIoTForge's `ARCHITECTURE.md`, the parts that describe the **device**: the runtime (agent, payload, worker process and its IPC, heartbeat, watchdog, restart policy), the OTA state machine (downloading, verifying, staged, probation, running, failed, rolledBack, last known good), the DPS paths (X509Dps over MQTT, X509DpsAdr over HTTPS with the CSR), certificate handling, and the PySharp primitives and gaps found by the spike. Keep the verified-live / verified-offline / not-verified labels exactly as they are in the source. Do not delete anything from Forge's file: another session handles that; copy and adapt.
- `docs\CONTRACT.md`: one page. What PyGate implements for Forge, with the Forge sections to read: DPS registration and the two families, twin schema (desired and reported properties), telemetry schema and encodings (JSON now; Parquet when step 03B is done), direct methods, OTA artifact and signature (ECDSA P-384 / SHA-384, IEEE P1363 hex, over the exact script bytes; the device holds only the public key), the device folder layout. State that **Forge owns the contract** (`AzureIoTForge\docs\DEVICE-CONTRACT.md`, being written by another session; until it exists, ARCHITECTURE §2 to §6) and that any change to a format is a cross-project change that the owner coordinates.
- `ROADMAP.md`: the device-side steps with their real status, copied from the Forge ROADMAP rows 02, 03 (part B), 04, 11 (agent), plus new items for this project. `OPENPOINTS.md`: move here the "PySharp gaps" and "PySharp changes" sections, the Step 04 and Step 11 agent sections, and the open decisions about the device; leave the Forge-only content there.

**T7 — git.** `git init` in this folder (one repository per gateway). Make **no commit** unless the owner asks; list what would be committed.

## 5. The contract, in short (do not change a format on your own)

A device managed by Forge: registers with DPS (family `X509DpsAdr`: bootstrap certificate plus a CSR sent over HTTPS, the issued chain used for the hub; family `X509Dps`: one certificate; family `Direct`: SAS or self-signed certificate), connects to the hub over MQTT 3.1.1 on 8883 with server verification always on, sends telemetry (JSON now; properties `alarmState`, `encoding`, `schema`; body `ts, deviceId, temperature, setpoint, payloadVersion`), reads desired properties, reports `agent`, `payload{name,version,status,...}` and `alarmState`, implements the direct methods, and applies signed payloads from `desired.payload` or the `applyPayload` method with probation and rollback. Those are verified formats; the evidence is in the Forge documents named in section 2.

## 6. Rules specific to PySharp

PySharp is a separate project (`D:\Dev\Science\PySharp`). **The owner authorised modifying PySharp when the device needs a missing feature or a bug is found** (done so far: datetime, `os.replace`, `hmac.compare_digest`, `Timer.join`, `bytes.fromhex`, `pysharp_signing`, `!=` derived from `__eq__`, `open()` on a locked file raising a catchable error). Rules for such changes: only when the device truly needs it; minimal and generic (no IoT-specific code in PySharp); follow PySharp's own conventions, tests and docs (README, USAGE, ARCHITECTURE, ROADMAP, RELEASE_NOTES); pack to `D:\Dev\NuGetLocalFeed` with a new `-net11-preview.n` version; **do not publish; do not update the global tool without asking**; record every PySharp file touched in `OPENPOINTS.md` ("PySharp changes"). For anything else, record the gap with a minimal reproducer under "PySharp gaps" and work around it in the agent. PySharp ignores `PYTHONPATH` and does not set `__file__` for the entry script: the code already works around both.

## 7. What not to do

- Do not modify AzureIoTForge, AzureIoTShipyard or PySharp (except under section 6, with approval). Do not delete anything in AzureIoTForge.
- Do not register a device with DPS, do not create, change or delete Azure resources, do not start a device without the owner's OK for that moment (section T5). Never turn `allowDpsRegistration` on.
- Do not print or log private keys, SAS tokens or connection strings. Keys stay in `D:\Configurations\AzureIoTForge\devices\<id>\`.
- Do not stop processes you did not start; ask the owner. Do not run `dotnet test` on several projects at once, nor the soak next to other work.
- Do not rename formats, topics or file names that Forge relies on (device folder files, `agent-config.json` keys, `issued-chain.expiry.json`) without an entry in `OPENPOINTS.md` and the owner's decision.

## 8. Decisions the owner has not made (use the default, write it down in OPENPOINTS)

| Decision | Default |
|---|---|
| Where the device folders and keys live | stay in `D:\Configurations\AzureIoTForge\devices` (legacy), reachable through `PYGATE_DEVICES_ROOT` |
| Where the paho folder lives | `D:\Configurations\AzureIoTForge\python-site` (unchanged); new installs through `PYGATE_PYTHON_SITE` |
| Which side writes `agent-config.json` for a new device | PyGate (`ops\Seed-DeviceConfig`); Forge writes only the credential package |
| Repository | its own, `git init`, no commit |

## 9. Acceptance and report

Done when: `AzureIoTPyGate.slnx` builds with 0 warnings and no `bin`/`obj` in the repository; the xUnit project passes without the soak, and the soak passed once alone; the Python runner passes; all four launchers pass their dry-run and guard cases; `Preflight.ps1` runs; the documents of T6 exist; `grep` finds no unexplained reference to AzureIoTForge paths; no leftover `pysharp` process; `git init` done, nothing committed. Report to the owner in **Italian**, listing for every item `verified-live`, `verified-offline` or `not-verified`, what you did not do (and why), and the exact files you wrote. Say plainly that the originals are still in AzureIoTForge and may now be removed by the other session.
