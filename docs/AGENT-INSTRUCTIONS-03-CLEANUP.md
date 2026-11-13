# Round 3 — documentation cleanup and agent code review

Audience: a coding agent working in this repository. Read `AGENTS.md` first; its rules win over this file. Communicate with the owner in Italian; write code, docs and commit-free notes in English.

## Context

A check on 2026-11-12 found: build 0 warnings/0 errors; `dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak"` = 30 passed, 0 skipped (verified-offline, log in `D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\check-test.log`). The target is `net11.0`, but several files still describe `net10.0`, test counts are stale, and the code in `src/agent` has not had a full review.

## Hard constraints

- No commit, no publish, no global tool install/update, no Azure change, no DPS registration, no real device start, no soak. Never enable `allowDpsRegistration`.
- Do not modify sibling repositories (AzureIoTForge, PySharp). Do not change wire formats.
- Never store or print credentials, keys, SAS tokens or connection strings.
- All logs, scratch and reports go to `D:\Dev\BuildArtifacts\AzureIoTPyGate` (`logs\`, `tmp\`), never into the repository. Move anything you remove to `D:\Dev\BuildArtifacts\trash` and tell the owner.
- Run test projects serially. Before running tests, confirm with `Get-Process dotnet,pysharp,python` that no other session is testing or running devices; do not stop processes you did not start.
- Report evidence as verified-live, verified-offline or not-verified. Do not claim anything about a UI you did not open.

## Tasks (do them in order, one at a time)

### T1 — Remove stale net10.0 statements (all `net11.0`, no multi-targeting)

| File | Fix |
|---|---|
| `README.md` line ~17 | Replace "Session constraints require net10.0 ..." with a statement that the project targets only `net11.0`; keep the layout note (solution in `src`, docs at root) and the PySharp/artifacts sentences. |
| `USAGE.md` line ~7 | Prerequisite becomes ".NET 11 SDK (`net11.0`)"; drop the sentence that PySharp's .NET 11 runtime "does not change the target framework". |
| `scripts/Preflight.ps1` line ~49 | Check label `.NET 11 SDK`; verify that an SDK with major version 11 is installed; update the detail text. |
| `ops/Validate-Ops.cs` line 1 | `#:property TargetFramework=net11.0`. |

Exit check: `Grep` for `net10|\.NET 10` over `*.md,*.cs,*.csproj,*.ps1` returns only intentional historical mentions (ROADMAP line "Historical round 1 evidence", `docs\AGENT-INSTRUCTIONS*.md`). Run `.\scripts\Preflight.ps1` (read-only) and report its output faithfully, including any check that fails for unrelated reasons.

### T2 — Make test counts and status consistent

- README and ROADMAP (S1 bullet and "Migration verification") still say "29 passed, 1 skipped". Current evidence is 30 passed, 0 skipped. Keep the old number only as historical evidence, labelled with its date, and add the new result with its date and log path.
- README mentions the skipped historical-log test; ROADMAP says it was reserved for S3. Reconcile with the current facts: the test no longer skips (inspect `tests/AzureIoTPyGate.Tests/UnitTest1.cs`, `InspectSyntheticLogsWithoutSecrets`, which uses synthetic logs). Do not claim it checks live reliability.
- ROADMAP "NEXT (agreed 2026-11-12): round 2" must reflect that S1 is done and S2–S8 are pending; add a pointer to this file as round 3.
- Do not mark S2–S8 as done. Soak, full launcher guard matrix and live checks stay not-verified.

### T3 — Fix references to files that do not exist here

`ARCHITECTURE.md` and `ROADMAP.md` cite Forge-era paths such as `device/agent/...`, `device/tests/...`, `src/AzureIoTForge.DeviceAgent.Tests` and `docs/OPENPOINTS.md`.
1. List every such path (`Grep` for `device/`, `device\\`, `AzureIoTForge.DeviceAgent`, `OPENPOINTS`).
2. Where the evidence is historical, keep the text but mark it "(historical Forge path; now `src/agent/...` / `tests/...`)" with the real current path. Where it is a current instruction, change it to the real path.
3. Do not rewrite or drop historical evidence rows; only clarify paths.

### T4 — Code review of `src/agent` (read-only findings, no code change in this task)

Files: `atomic_io.py`, `atomic_io_worker.py`, `config.py`, `dps.py`, `main.py`, `methods.py`, `mqtt_transport.py`, `ota.py`, `payload_worker.py`, `runtime.py`, `twin.py`, `worker_runtime.py`; also `src/tools/make_artifact.py` and the launchers in `scripts/` and `ops/`. Read every file completely.

Focus on:
- OTA: signature verification order (verify before write/exec), hash check, rollback path, version downgrade handling, tampered or oversized manifests, path traversal in payload file names.
- Secrets: nothing logged or written that contains keys, SAS tokens, private key material or connection strings; redaction in worker output.
- TLS/DPS/MQTT: server verification always on (port 8883, hostname check, `CERT_REQUIRED`), no code path that disables it, `allowDpsRegistration` default false.
- Concurrency and files: heartbeat file and atomic writes under Windows sharing violations, worker restart loop, watchdog timeouts, resource leaks.
- PySharp constraints: no use of unsupported features listed in ROADMAP "PySharp gaps" (`PYTHONPATH`, `__file__` in entry scripts, `os.replace` assumptions, `datetime.now(timezone.utc)`).
- Error handling that swallows exceptions silently.

Output: a findings list written to `D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\agent-review-2026-11-12.md` (not in the repo). For each finding: file and line, severity (high/medium/low), one-sentence defect, concrete failure scenario, confidence (confirmed by reading / plausible). Only report real defects; do not pad with style remarks. Summarize the counts to the owner and ask which findings to fix. Do not fix anything in T4 without the owner's answer; if the owner approves, each fix needs a regression test in `tests/python` and the xUnit wrapper.

### T5 — Final verification

1. `dotnet build src/AzureIoTPyGate.slnx` — expect 0 warnings, 0 errors.
2. `dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak"` after the process check; report the exact pass/fail/skip counts and duration. If a test fails, rerun it once to detect flakiness (heartbeat-idle is a known flaky case), and report both results; do not hide the failure.
3. Confirm no `bin`/`obj` directories inside the repository (`Glob **/bin`, `**/obj` excluding nothing) and no stray `.nupkg`, log or scratch files.
4. Record the results in the "Round 2 execution evidence" style in `ROADMAP.md` under a new "Round 3 execution evidence" heading, listing changed files and what stays not-verified.

## Report to the owner (Italian)

Short: what changed (file list), test result with evidence label, review finding counts, and what remains not-verified (soak, launcher guards, live Azure, real device). No commit was made.
