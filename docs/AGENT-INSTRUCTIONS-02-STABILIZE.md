# AzureIoTPyGate — stabilize and verify the migrated project (round 2)

Written for: an AI coding agent working in `D:\Dev\2026\IoTHub\AzureIoTPyGate`. The owner (Marco) chats in **Italian**; code, comments, commit messages and documents are in **English**. Use they/them for people whose pronouns are not stated. Read `AGENTS.md` first, then this file in full. Do the tasks in order, one at a time, each finished before the next. `docs\AGENT-INSTRUCTIONS.md` (round 1) is done: do not redo it.

## 1. Where the project stands (2026-11-12)

Round 1 bootstrapped the project: layout, `AGENTS.md`, README, USAGE, ARCHITECTURE, ROADMAP, ops scripts, the four launchers, `git init` with **no commit**. Reported by that round: build green with 0 warnings, 29 xUnit tests passing, 1 skipped, no leftover `pysharp` process. Open items it recorded: target framework `net10.0` (wrong, see S1), one intermittent failure in the heartbeat-idle test (S2), one test skipped because historical logs were not migrated (S3), the 10-minute soak never run (S4), the C# launcher and the full guard matrix never verified (S5), seed and install tools only statically checked (S6), `OPENPOINTS.md` and `docs\CONTRACT.md` missing (S7).

**This round has one purpose: make the project verifiably complete, so the owner can authorise removing the originals from AzureIoTForge (that removal is called T10 in Forge and waits for the owner).** The exit criteria are in section 5. Nothing here depends on the global review of who owns credentials, which the owner has not finished: do not touch that question.

## 2. Hard rules (also in `AGENTS.md`)

- **.NET 11 only (`net11.0`).** No retargeting, no multi-targeting. If a rule of your own session seems to force another version, **stop and tell the owner**: do not keep `net10.0` silently.
- **Do not change Azure resources and do not register a device with DPS.** `allowDpsRegistration` stays `false`. Read-only queries are fine.
- **Do not start a real device against the hub without the owner's explicit OK for that moment**: IoT Hub allows one connection per device id, so a real start can kick a device that the owner is running elsewhere.
- Do not modify AzureIoTForge, AzureIoTShipyard or PySharp (PySharp only under the rules in `AGENTS.md`). Never write a path to a sibling project into code: the folders will move under one new parent folder; use paths relative to this repository with an environment variable override.
- Generated output and scratch go to `D:\Dev\BuildArtifacts\AzureIoTPyGate`; nothing generated inside the repository. Never delete from `BuildArtifacts` outright: move to `D:\Dev\BuildArtifacts\trash` and say so.
- No secrets in the repository; do not print private keys, tokens or connection strings.
- **No commit** unless the owner asks. List what would be committed.
- Run **one test project at a time**, never two sessions' tests together. Do not stop processes you did not start. After every test run `Get-Process pysharp` must show nothing left over.
- Report faithfully: `verified-live`, `verified-offline`, `not-verified`. Say exactly what you ran and what you did not.

## 3. Tasks, in order

**S1 — move to `net11.0`.** Change `TargetFramework` in `Directory.Build.props` and in `tests\AzureIoTPyGate.Tests\AzureIoTPyGate.Tests.csproj` (the original Forge project was `net11.0`, and so is the installed SDK `11.0.100-rc.*`). Build with 0 warnings; run the non-soak tests. If something only works on `net10.0`, report exactly what and why, and wait for the owner. Remove the sentence in `ROADMAP.md` that justifies `net10.0` and record the result.

**S2 — the heartbeat-idle flakiness.** Reproduce first: run the heartbeat-idle test alone 20 times in a row on an idle machine and record pass/fail and timings. Then read the test and the code it exercises (`src\agent\worker_runtime.py`, `payload_worker.py`; the heartbeat file is touched periodically and on every callback, timeout `workerHeartbeatTimeoutSec` default 30 s). Typical causes to check, in this order: a timeout in the test that is too tight for a loaded machine (make it proportional and configurable, not fixed); state shared with another test (use a scratch folder per test); a worker left alive by an earlier test; a race between the heartbeat write and the watchdog read (the earlier `os.replace`-based heartbeat failed with `UnauthorizedAccessException` after 3m49s: the final version writes directly and protected: re-read why). Fix the cause, add a test that reproduces it if you can, and repeat the 20-run check: **20 of 20 must pass**. If you cannot reproduce it, say so and record the evidence; do not declare it fixed.

**S3 — no skipped test.** `UnitTest1.cs` skips a test because historical live logs were not migrated. Replace it with a self-contained test that carries the minimum synthetic fixture it needs (a small redacted sample created by the test, in the scratch folder), or, if the test only ever checked the old Forge evidence, remove it and move that claim to the documentation as historical evidence. The non-soak run must report **30 passed, 0 skipped** (or the exact new count, explained).

**S4 — the 10-minute soak, once, alone.** Before it, check there is nothing else running (`Get-Process pysharp`, `Get-Process dotnet`, and ask the owner whether other sessions are stopped), because the soak measures worker restarts and a loaded machine biases it. Run `dotnet test tests\AzureIoTPyGate.Tests --filter "Category=Soak"` once. Criterion: three concurrent loopback agents, **0 worker restarts, 0 payload errors**, about 580 messages each, no residual process. The criterion is **not** to be relaxed; if it fails, record per agent the restart reasons (`workerReason`, `workerRestarts` in the events), and diagnose before changing code. If it passes, run it a second time to catch intermittency, and report both.

**S5 — the four launchers, full matrix.** Scripts: `Start-Device.ps1`, `start-device.cmd`, `StartDevice.cs`, `start_device.py`. With a scratch configuration root (`FORGE_CONFIG_ROOT` or `PYGATE_DEVICES_ROOT`, as implemented) holding fake devices, check each launcher for: bad id, missing config, config without `allowDpsRegistration: false`, invalid duration, `--dry-run` with and without a duration, `pysharp` missing from PATH, **already running**. To test "already running" **without any real device**: start a harmless long-running PySharp script whose command line contains the fake device's `...\devices\<id>\agent-config.json` path as an argument (the guard looks for a `pysharp.exe` process with that path in its command line); the launcher must refuse with exit code 3; stop your own helper afterwards. The matrix must pass for **all four** launchers, including the C# one (`dotnet run scripts\StartDevice.cs -- ...`). A real 12-second start of a real device is allowed **only with the owner's explicit OK for that moment**: ask once, name the device and say it sends telemetry to the hub (no registration, no Azure change), and check that device is not already running.

**S6 — `ops\` verified by running, not only by reading.** (a) `Seed-DeviceConfig`: generate a synthetic credential package in a scratch folder (self-signed test certificates created with `openssl` or .NET, a hand-written `dps-registration.json` with the same shape as a real one: `registrationState.assignedHub`, `deviceId`, `issuedCertificateChain` as a list of base64 strings of PEM text, `status: assigned`), run the tool, and check the three outputs (`agent-config.json` with `allowDpsRegistration: false`, `agent-state.json` with the cached assignment, `issued-chain.expiry.json` in the format `{"notAfter":"...Z","certificateSha256":"<sha256 of issued-chain.pem>"}`); run it twice (idempotent, never overwrites, refuses unsafe existing files); then start the agent in loopback mode from the produced config to prove it is readable. (b) `Install-Dependencies`: run it against a scratch site folder (it may download `paho-mqtt==2.1.0`; no global install) and prove `import paho` works from a PySharp script using that folder. Add these as tests or as a documented runnable check; keep real credentials out of every fixture.

**S7 — documents the owner asked for in round 1 and are missing.**
- `OPENPOINTS.md` at the root: split the open decisions and the PySharp gaps and changes out of `ROADMAP.md` into it; keep `ROADMAP.md` for steps and status.
- `docs\CONTRACT.md`, one page: what PyGate implements for Forge, as a **map from each clause to the test that proves it** (DPS registration in the two families; twin desired and reported; telemetry body, application properties and the device id coming from the hub system property; direct methods; signed OTA with ECDSA P-384 / SHA-384, IEEE P1363 hex over the exact script bytes; the credential package files read; `issued-chain.expiry.json`). It names Forge's `DEVICE-CONTRACT.md` as the owner of the contract (relative reference, no absolute path), and says that a format change is a cross-project change coordinated by the owner.
- Update `README.md` and `USAGE.md` (installation first) to the final state; remove "partial migration" wording only if S1 to S6 are green.

**S8 — close the round.** Update `ROADMAP.md`: the exit checklist of section 5 with a status per item. List the files you wrote or changed. `git status` must show only your changes; no commit.

## 4. Do not

- Do not start T10 or touch anything in AzureIoTForge. Do not change formats, topics, file names or config keys that Forge relies on (device folder files, `agent-config.json` keys, `issued-chain.expiry.json`) without an entry in `OPENPOINTS.md` and the owner's decision.
- Do not create, change or delete Azure resources; do not register a device; do not enable `allowDpsRegistration`.
- Do not start a real device without the OK of section S5. Do not run the soak next to other work.
- Do not work on Parquet in this round (step 03 is a separate, later task that touches PySharp and Forge together).

## 5. Exit criteria, the owner's gate to remove the originals from Forge (all must be true, each reported)

| # | Criterion | Needed |
|---|---|---|
| 1 | Builds on `net11.0`, 0 warnings, no `bin`/`obj` in the repository | verified-offline |
| 2 | Non-soak tests green, 0 skipped (or explained), heartbeat test 20 of 20 | verified-offline |
| 3 | Soak passed once alone with 0 restarts (twice if possible) | verified-offline |
| 4 | Four launchers pass the full guard matrix | verified-offline |
| 5 | `Seed-DeviceConfig` and `Install-Dependencies` verified by running | verified-offline |
| 6 | `OPENPOINTS.md`, `docs\CONTRACT.md`, README and USAGE final | documents |
| 7 | One real 12-second start from a launcher | verified-live, **only if the owner OKs it** |
| 8 | No leftover `pysharp` process, no secret in the repository | verified-offline |

## 6. Report

Report to the owner in **Italian**: per task S1 to S8 what you did, with `verified-live` / `verified-offline` / `not-verified`; the numbers (test counts, the 20-run result, soak messages and restarts per agent); what you did not do and why; the files written; and an explicit sentence: "the exit criteria are / are not all met; the originals in AzureIoTForge may / may not be removed (that decision is the owner's)".
