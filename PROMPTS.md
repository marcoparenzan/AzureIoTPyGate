# Prompts

Log cronologico delle richieste, scritto da RalfAI.

## 1. "Lavora in D:\Dev\2026\IoTHub\AzureIoTPyGate. Leggi docs\AGENT-INSTRUCTIONS.md per intero ed eseguilo…"

Lavora in D:\Dev\2026\IoTHub\AzureIoTPyGate. Leggi docs\AGENT-INSTRUCTIONS.md per intero ed eseguilo.

_2026-11-12 00:23_

## 2. "Lavora in D:\Dev\2026\IoTHub\AzureIoTPyGate. Leggi AGENTS.md e poi docs\AGENT-INSTRUCTIONS-02-STABIL…"

Lavora in D:\Dev\2026\IoTHub\AzureIoTPyGate. Leggi AGENTS.md e poi docs\AGENT-INSTRUCTIONS-02-STABILIZE.md per intero ed eseguilo, un compito alla volta (S1-S8). Il giro 1 (docs\AGENT-INSTRUCTIONS.md) è già fatto: non rifarlo.

_2026-11-12 01:29_

## 3. "Confermato: nessuna altra sessione di test e nessun dispositivo sono in esecuzone. li otto processi…"

Confermato: nessuna altra sessione di test e nessun dispositivo sono in esecuzone. li otto processi dotnet sono i server
Copilot/VS Code per l'aggiornamento di .NET (inattivi) e i server MSBuild/VBCSCompiler rimasti in memoria: non sono
test e non vanno fermati. La macchina resta inattiva durante S2 e S4. Questa conferma non autorizza l'avvio di dispositivi
reali (serve un OK separato). Procedi con S2 e poi con gli altri compiti in ordine.

_2026-11-12 01:43_

## 4. "Read AGENTS.md, then docs/AGENT-INSTRUCTIONS03-CLEANUP.md in this repository, and execute tasks T1 t…"

Read AGENTS.md, then docs/AGENT-INSTRUCTIONS03-CLEANUP.md in this repository, and execute tasks T1 to T5 in order, one at a time.

Rules that override everything else:
- No commit, publish, global tool install/update, Azure change, DPS registration, real device start or soak. Never enable allowDpsRegistration.
- Do not modify sibling repositories (AzureIoTForge, PySharp). Do not change wire formats.
- Never store or print credentials, keys, SAS tokens or connection strings.
- All logs, scratch and reports go to D:\Dev\BuildArtifacts\AzureIoTPyGate, never into the repository. Move anything you remove to D:\Dev\BuildArtifacts\trash and tell me.
- Before running tests, check with Get-Process dotnet,pysharp,python that nobody else is testing o running devices. Do not stop processes you did not start. Run tests serially.

T4 is read-only: write the findings to D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\agent-review-2026-11-12.md, report the counts to me, and wait for my answer before fixing anything.

Report every result as verified-live, verified-offline or not-verified. Say what failed, what you skipped and what you did not test. Do not claim a UI works if you did not open it.

Write code and documentation in English. Talk to me in Italian. Finish with a short report: changed files, test counts with evidence, review findings, what remais ot-verified.

_2026-11-12 20:28_

## 5. "Read AGENTS.md, docs/AGENT-INSTRUCTIONS-03-CLEANUP.md and D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\…"

Read AGENTS.md, docs/AGENT-INSTRUCTIONS-03-CLEANUP.md and D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\agent-review-2026-11-12.md. T1 to T4 are done. The owner approves fixes for findings 2, 3, 4 and 5 only, then T5.

Fix, one finding at a time, in this order:
- Finding 2: Agent.start() must never execute an unverified payload. Make ota.bootstrap() distinguish "no payload active yet" from "rollback failed" (for example three explicit outcomes). On a failed restore or failed rollback, do not call runtime.load() on the rejected file; stay in a safe state without a payload, report it through the existing reported-twin/event mechanim, and keep telemetry running only if that is already possible without a payload.
- Finding 5: explicit rollback must restore the previous accepted version, not the current one. Keep a previous-good descriptor (for example lastGood plus previousGood) without changing the on-disk state format in an incompatible way: old state files must still load. If no previous version exists, rollbackPayload must report failure honestly, not success.
- Finding 3: a payload that hangs in on_tick during desired-property OTA probation must trigger rollback to the last good payload. Separate real infrastructure failures from payload timeouts if needed, but keep the existing zero-restart behaviour for healthy payloads and do not weaken the heartbeat logic.
- Finding 4: after a worker is replaced, run on_start (and replay the desired initialization) before retrying the failed callback.

For each fix add a regression test in tests/python (and register it in the xUnit wrapper in tests/AzureIoTPyGate.Tests), using GUID scratch directories under D:\Dev\BuildArtifacts\AzureIoTPyGate\tmp. Show that each new test fails without the fix when hat is practical.

Do NOT change: the wire format, the manifest or signature scheme (finding 1), the launchers (finding 9), IPC retention and redaction (6, 7), atomic writes (8), pruning (10), the OTA timeout (11). Do not modify PySharp or Forge. For finding 1, only add a short open point to ROADMAP.md: the signature covers script bytes only, a fix changes the contract and must be agreed with AzureIoTForge.

Then T5: dotnet build src/AzureIoTPyGate.slnx (0 warnings, 0 errors); check with Get-Process dotnet,pysharp,python that no other session is testing or running devices; run dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak" once, serially. If a test fails, rerun once and report both results. Confirm no bin/obj, .nupkg, log or scratch files inside the repository; the .ralfai folder is not yours, report it but do not delete it.

Update ROADMAP.md with a "Round 3 xecution evidence" section (findings fixed, changed files, test counts, what stays not-verified) and keep ARCHITECTURE.md and USAGE.md accurate if behaviour changed.

Rules that override everything: no commit, publish, global tool install/update, Azure change, DPS registration, real device start or soak; never enable allowDpsRegistration; never store or print credentials, keys, SAS tokens or connection strings; all logs and scratch go to D:\Dev\BuildArtifacts\AzureIoTPyGate; move anything you remove to D:\Dev\BuildArtifacts\trash and tell me; do not stop processes you did not start.

Report every result as verified-live, verified-offline or not-verified. Write code and docs in English; talk to me in Italian. Finish with a short report: changed files, per-finding result, test counts with evidence, what remains not-verified.

_2026-11-12 22:41_

## 6. "Read AGENTS.md, then docs/AGENT-INSTRUCTIONS-04-LOGGING.md, and implement it fully: diagnostic loggi…"

Read AGENTS.md, then docs/AGENT-INSTRUCTIONS-04-LOGGING.md, and implement it fully: diagnostic logging with a -LogLevel flag (off, info, debg trace) for the device agent and the three launchers, with tests, docs and the verification described there.

Before starting, check ROADMAP.md for the round 3 security fixes (findings 2 to 5): if they are still in progress, stop and tell me. Do not overwrite anyone's edits.

Overriding rules: no commit, publish, global tool install/update, Azure change, DPS registration, real device start or soak; never enable allowDpsRegistration; never print secrets; logs and scratch only under D:\Dev\BuildArtifacts\AzureIoTPyGate; move removals to D:\Dev\BuildArtifacts\trash and tell me; do not stop processes you did not start; run tests serially after checking no other session is running devices.

Report results as verified-live, verified-offline or not-verified. Code and docs in English, talk to me in Italian. Finish with changed files, test counts, sample log lines, and what remains not-verified.

_2026-11-12 23:08_

## 7. "Read AGENTS.md first. Task: find and fix the cause of an intermittent failure in test_ota_desired_ti…"

Read AGENTS.md first. Task: find and fix the cause of an intermittent failure in test_ota_desired_tick_watchdog_rollback (tests/python/approved_ota_cases.py, wrapped by tests/AzureIoTPyGate.Tests/UnitTest1.cs). Do not change anything else.

Known evidence (owner check, 2026-11-12, verified-offline): run alone and serially with
dotnet test tests/AzureIoTPyGate.Tests --no-build --filter "DisplayName~test_ota_desired_tick_watchdog_rollback"
it failed 1 time in 10 (logs: D:\Dev\BuildAtifacts\AzureIoTPyGate\logs\check-watchdog-1.log to -10.log). In the full suite it failed once in three rounds. The traceback says "line 230" which is `runtime.stop()` in the finally block, not an assertion, so PySharp attributes the line imprecisely and the failing assertion is unknown. Most assertions in this test have no message.

Steps, in order:
1. Add a diagnostic message to EVERY assert in that test (and in helpers it relies on if they assert), showing the values that matter: ota.status, ota.error, runtime.last_error, runtime.worker_restarts, runtime.errors, store.data["current"] and ["lastGood"] versions, clock.value, and whether the worker processes are alive. Never include secrets or key material. Keep the test logic unchanged in this step.
2. Check with Get-Process dotnet,pysharp,python that nobody else is testing or running devices (an IDE Python extension process is not a test). Then run the test 30 times, serially, with a loop that saves each run to D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\flaky-watchdog-<n>.log. Report pass/fai counts and WHICH assertion fails with its message.
3. Find the root cause by reading the code involved (src/agent/runtime.py call(), worker_runtime.py, ota.py tick_result and probation, the test's fake clock and ReadFailureWorker wrapper). Decide which it is: a test race (for example real-time watchdog and heartbeat versus the fake clock, or worker startup time under load), or a real defect in the agent (probation, restart counters, state after replacement). Explain the reasoning with evidence from the failing logs.
4. Fix the cause. Rules for the fix:
   - Do not lengthen timeouts, add sleeps or retries just to hide the failure.
   - Do not weaken security behaviour: OTA signature verification, rollback on payload hang, the heartbeat that keeps a healthy idle worker alive, and the rule that infrastructure failures do not count against a candidate.
   - If the cause is in the test (it mixes real time and the fake clock), make the test deterministic.
   - If the cause is a real agent defect, fix it minimally and add a separate regression test that reproduces it deterministically; tell the owner in the report because it is a behaviour change.
   - Do not change wire frmats, PySharp or sibling repositories.
5. Verify: run the fixed test 30 times serially, all must pass; then run dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak" once, serially. If anything fails, rerun that test alone, report both results honestly and do not mark the suite stable.
6. Update ROADMAP.md ("Round 3 execution evidence" or a new "Round 5" heading): root cause, fix, changed files, the 30-run counts with evidence labels. Remove the "flakiness remains open" statements only if the 30/30 evidence supports it.

Overriding rules: no commit, publish, global tool install/update, Azure change, DPS registration, real device start or soak; never enable allowDpsRegistration; never print credentials, keys, SAS tokens or connection strings; all logs and scratch under D:\Dev\BuildArtifacts\AzureIoTPyGate; move anything you remove to D:\Dev\BuildArtifacts\trash and tell me; do not stop processes you did not start; leave th .ralfai folder alone.

Report every result as verified-live, verified-offline or not-verified. Code and docs in English, talk to me in Italian. Finish with: root cause, changed files, 30-run counts before and after, full-suite result, what remains not-verified.

_2026-11-12 23:51_
