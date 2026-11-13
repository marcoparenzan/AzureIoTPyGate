# Usage

## Diagnostic logging

Levels are `off`, `info` (default), `debug`, `trace`. Launchers accept `-LogLevel`; C# and Python also accept `--log-level`. The launcher flag overrides config `logLevel`, then `info`; the agent itself resolves `PYGATE_LOG_LEVEL` before config. Unknown values are rejected. These example **live starts require separate owner permission**:

```powershell
.\scripts\Start-Device.ps1 device01 -LogLevel debug
pysharp run scripts/start_device.py device01 -LogLevel trace
dotnet run --file scripts/StartDevice.cs --artifacts-path D:\Dev\BuildArtifacts\AzureIoTPyGate\launcher -- device01 -LogLevel trace
```

Add `-DryRun` (PowerShell) or `--dry-run` (Python/C#) to check without starting. The CMD wrapper remains unchanged; use the three launchers above for level flags.

Info reports lifecycle, provisioning/cache, MQTT connection reasons, OTA verification/probation/rollback, worker recovery and state saves. Debug adds message metadata, callback durations and retry/backoff. Trace adds recursively redacted message bodies, desired version/changed keys, method names/RIDs, twin statuses and MQTT PUBACK latency. At debug/trace, the agent writes a startup line naming the authoritative log file to the inherited launcher console.

`logDir` must be an absolute directory under `D:\Dev\BuildArtifacts\AzureIoTPyGate`. Default is a `logs` directory beside an external state file within that root, otherwise `logs\<registrationId>` under the root, one folder per device (a plain `logs` folder only when the registration id is missing or unusual). At `info` the agent prints one `log startup` line with the exact log file path; `state saved` is a `debug` event. This restriction overrides the general state-relative default for credential folders elsewhere. Files are `agent.0.log` through `agent.3.log` by default, each capped at 262144 bytes. Optional `logMaxBytes` (1024–1048576), `logFileCount` (1–16), `logQueueSize` (1–4096; default 256) control bounds. Use a distinct directory per device. Rotation overwrites bounded numbered slots, avoiding rename/replace dependencies; chronological order is determined by timestamps, not filenames.

Trace is for local diagnosis: it may contain device data and must stay outside the repository. Redaction is not authorization to collect credentials. Sensitive keys are replaced recursively, URL query/fragment removed, long strings/bodies truncated. Never commit diagnostic output. A bounded asynchronous writer keeps file/console I/O off callbacks; on failure console remains available when possible, and queue pressure can drop diagnostics.


## Payload recovery behavior (round 3)

Startup hash-checks the trusted initial local payload before worker execution; signed OTA restores additionally verify the existing signature. A failed restore or rollback leaves the agent connected without a payload and reports failure through existing twin/event fields. It does not fall back to executing the rejected file or fabricate telemetry.

`rollbackPayload` restores the previous accepted version; during probation it restores lastGood. With no previous accepted version it reports conflict (409), not a successful no-op. Optional `previousGood` state is backward-compatible with old state files. Failed restoration enters the safe no-payload state.

Worker replacement initializes `on_start`, replays full desired settings, then retries the failed callback once. Two consecutive tick watchdog expirations after successful initialization trigger probation rollback; transport/read failures remain infrastructure faults. Independent heartbeat behavior is unchanged.


## Hardening (round 7)

- **Worker IPC files.** Each callback writes a request and a response file in the worker folder. Only the newest `workerIpcRetain` pairs (default 5, range 0 to 1000) are kept; older ones are removed after each consumed callback, so storage stays bounded. Retained files contain device state and desired properties, so keep the worker folder outside the repository and with owner-only access.
- **State saves.** A failed periodic state save (for example a Windows file lock) is logged as `save_failed` and retried at the next 5 s interval. After 12 consecutive failures the agent stops with the error.
- **OTA pruning.** Older payload versions are moved to `<payload folder>/<name>/archive`, on the same volume, never deleted. A failed prune is logged as `prune_failed` and never undoes an accepted update.
- **`applyPayload` timeout.** `otaMethodTimeoutSec` now covers download, verification and activation, not only the probation ticks.
- **Duplicate-device guard.** All launchers refuse to start when a `pysharp` process already uses the same config file path, whatever the devices root is named, or the legacy `\devices\<id>\` layout.

## Operational runbook

### Installation first

Run commands below from the repository root on Windows. Prerequisites: a **.NET 11 SDK** (`net11.0`), PowerShell 5.1 or 7, and the already approved PySharp tool on PATH. The reference environment has `pysharp` **2.5.1-net11-preview.10**. Do not install/update a global tool without separate owner approval. No sibling repository is required or modified.

First choose the stable, private import site. The following commands are instructions for an authorised operator, not actions already performed:

```powershell
$env:PYGATE_PYTHON_SITE = 'D:\Configurations\AzureIoTPyGate\python-site'
.\ops\Install-Dependencies.ps1 -WhatIf
# After approving the package installation:
.\ops\Install-Dependencies.ps1
$env:PYGATE_PAHO_SITE = $env:PYGATE_PYTHON_SITE
```

The installer pins **paho-mqtt==2.1.0**. PySharp's inspected install help says it writes `./site-packages` and has no `--target` option. The tool therefore installs with a fresh staging working directory under `D:\Dev\BuildArtifacts\AzureIoTPyGate\ops`, validates `paho/mqtt/client.py` and distribution metadata, and copies into a newly created stable site. It never invokes global installation or modifies device configs. A valid existing site is kept; an invalid or mixed-version existing site is refused, never replaced. A failed new/partial site and staging artifacts are retained for local inspection. Move unwanted artifacts to `D:\Dev\BuildArtifacts\trash`, not directly to deletion.

For an **offline copy** of an already installed site, use:

```powershell
.\ops\Install-Dependencies.ps1 -CopyFrom 'D:\Configurations\AzureIoTForge\python-site' -WhatIf
# Repeat without -WhatIf only after approval. The source above is a legacy site.
```

`-PythonSite` overrides `PYGATE_PYTHON_SITE`; absent both, the **legacy default** is `D:\Configurations\AzureIoTForge\python-site`. `PYGATE_PYTHON_SITE` chooses where this installer puts the package; it does not change the agent's import path. Each externally managed device config must explicitly set an **absolute** `pahoSite` to the chosen site (`main.py` inserts it as-is). Tests use `PYGATE_PAHO_SITE`. Do not rely on `PYTHONPATH`: PySharp ignores it.

Choose the external **devices folder itself**, not its parent:

```powershell
$env:PYGATE_DEVICES_ROOT = 'D:\Configurations\AzureIoTPyGate\devices'
```

All four launchers use `PYGATE_DEVICES_ROOT`, then the **legacy** `FORGE_CONFIG_ROOT\devices`, then the **legacy** `D:\Configurations\AzureIoTForge\devices`. Existing packages at the legacy location keep working. Credentials, configs, assignment state and logs must remain outside this repository. AzureIoTForge owns the credential/assignment **contract**; these operations do not register anything with DPS.

### Seed one existing credential package, offline

Prerequisite: a package already registered by the authorised provisioning owner, containing `dps-registration.json` with an **assigned** `registrationState`, `issued-chain.pem` (leaf first), `bootstrap-chain.pem`, `bootstrap-private-key.pem`, `device-private-key.pem` and `device.csr.pem`. Optional `issued-chain-01.pem`, when present, must match the first certificate in the bundle. Keys are checked for existence only, never read or printed. The certificate is parsed locally with .NET, without OpenSSL or Azure.

Provide the folder explicitly and a real, approved **X509DpsAdr** agent template supplying `idScope`, `globalEndpoint`, `payloadDir` and any optional OTA/paho settings. There is no built-in device list, implicit probe template or directory discovery. Replace these example paths with your package/template paths:

```powershell
.\ops\Seed-DeviceConfig.ps1 `
  -CredentialFolder "$env:PYGATE_DEVICES_ROOT\my-device" `
  -TemplateConfig 'D:\Configurations\AzureIoTPyGate\approved-agent-template.json' -WhatIf
# Review locally. Repeat without -WhatIf only when authorised to create missing files.
```

The tool creates **only missing** canonical files in that package:

- `issued-chain.expiry.json`: exactly `notAfter` (UTC `yyyy-MM-ddTHH:mm:ssZ`) and `certificateSha256` (lowercase SHA-256 of the **entire raw issued-chain.pem**, not the parsed leaf).
- `agent-state.json`: exactly `assignedHub`, `deviceId`, absolute `issuedCert`, and numeric Unix-seconds `adrIssuedAt` from certificate not-before. The agent computes `adrRenewAt` itself.
- `agent-config.json`: preserves the template settings and overrides the package-specific paths/identity exactly as the reference does, with **boolean `allowDpsRegistration: false` and `allowReprovision: false`**. Shared relative `payloadDir`, `payloadPublicKeyPem` and `pahoSite` are resolved against the template folder.

The config is created last. `CreateNew` and exclusive file access prevent overwriting, including a concurrent creator. Existing matching files are skipped; incompatible/unsafe retained metadata causes refusal **before any writes**. Expired, not-yet-valid or renewal-due certificates, invalid assignment identities and package junctions/reparse points are refused. No repair/force mode, key generation, DPS registration or Azure call exists. There is no multi-file transaction: if a write fails, inspect partial newly created files locally before retrying; never run the seed concurrently with a device or another seed. Existing state after legitimate operational renewal may no longer match not-before exactly, so this is initial seeding, not state repair or credential rotation.

Output is only generic status and canonical filenames, never JSON, keys, assignment values or exception bodies. The old `ops/seed-device-config.reference.py` is **historical reference only**: do not execute it (it contains fixed example devices and overwrites metadata). Use the PowerShell tool instead. The tool does not verify key/certificate pairing or server connectivity; those are not offline readiness evidence.

### Preflight and start a device (four launchers)

```powershell
.\scripts\Preflight.ps1
# Optional subset/explicit paths:
.\scripts\Preflight.ps1 -DeviceId my-device -DevicesRoot $env:PYGATE_DEVICES_ROOT -PythonSite $env:PYGATE_PYTHON_SITE
```

Preflight is **read-only**: SDK inventory, PySharp PATH/version query, pinned private paho metadata, devices root, each selected/readable config with explicit boolean DPS false, required paths, cached assignment, ADR renewal deadline, and process inventory. It prints a PASS/BLOCK table and exits **0 only without blockers**, otherwise **1**. Without `-DeviceId`, it checks every immediate device subfolder. Missing/invalid configs, missing paho/cache, due renewal, any active PySharp tool/agent/worker, or an unavailable process inventory block readiness. It does not print configs or process command lines, contact Azure, login, start devices, import Python code, write files or stop processes. This is not a TLS/key-pair/network test.

Use **exactly one** launcher for the same identity. First dry-run each form; dry-runs inspect local configs/processes but start no device:

```powershell
.\scripts\Start-Device.ps1 my-device -Seconds 120 -DryRun
.\scripts\start-device.cmd my-device 120 --dry-run
dotnet run scripts/StartDevice.cs -- my-device --seconds 120 --dry-run
pysharp run scripts\start_device.py my-device 120 --dry-run
```

Only after **explicit owner approval for that moment**, no existing instance and a successful preflight, remove the dry-run flag from your chosen command to launch. Omitting the duration runs until Ctrl+C. A live start sends telemetry using the cached assignment; it is **not offline**. Launchers find the agent in `src\agent`, reject unsafe DPS flags and attempt to reject duplicate instances, and restart on agent exit **75**. Guards are not a race-free lock: do not launch the same identity in parallel. Existing launcher duplicate detection uses a legacy `\devices\<id>\agent-config.json` command-line pattern; custom root names/process hosts may evade it. The broader preflight process blocker and coordination remain mandatory. Do not alter Azure or enable DPS to work around failed starts.

### Stop a device

**Preferred, from any console:** ask the agent to stop itself. This creates `stop.request` next to `agent-config.json`; the agent checks for it about once a second, shuts down cleanly (state saved, workers closed, log `stop_file_seen`, `shutdown`, `stopped`), removes the file and exits.

```powershell
.\scripts\Start-Device.ps1 my-device -Stop
pysharp run scripts\start_device.py my-device --stop
dotnet run scripts/StartDevice.cs -- my-device --stop
```

Each waits up to 30 s. Exit 0: stopped, or it was not running. Exit 4: still running after 30 s (then use Ctrl+C in its own console, or ask the owner; never a blanket `Stop-Process -Name pysharp`). A stale `stop.request` is removed at startup, so it never stops the next run. This works for an agent started by this version of the code; an older running agent ignores the file.

**Otherwise:** Use **Ctrl+C in the console you own** or an approved bounded duration, then wait for the launcher/agent and its workers to exit. Do not close another session's console, stop a process you did not start, or use blanket `Stop-Process -Name pysharp`. A restart request (75) can make the launcher immediately start again; stop the owning launcher cleanly. Read-only check afterward:

```powershell
Get-Process pysharp -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
.\scripts\Preflight.ps1 -DeviceId my-device
```

### Rotate the private paho site

1. Coordinate with all device/test owners; stop only your instances cleanly. Do not change a shared site while it is imported by running agents/workers.
2. Choose a **new empty site**, e.g. `D:\Configurations\AzureIoTPyGate\python-site-2.1.0-next`, and run the installer with `-PythonSite ... -WhatIf`. Then, when authorised, install/copy into that new site. A global package installation is never needed.
3. Through the approved external config-management process, point selected configs' absolute `pahoSite` and the test variable `PYGATE_PAHO_SITE` at the new site. Set `PYGATE_PYTHON_SITE` for future installs/preflight. The installer does not edit these files or environment persistence.
4. Run preflight and offline tests serially; live restart still needs separate permission. Keep the previous site for rollback: restore its path settings rather than merging files or deleting it. Archive unwanted generated artifacts to the mandated trash.

### Recover from a leaked worker

Pause new starts/tests/soaks. Use the preflight count and a local process inspector to establish PID, parent PID and ownership; **never publish command lines or config/credential contents**. Ask the owning session to stop its parent and allow normal cleanup first. If a worker remains, obtain permission and target only the **verified PID you started** (recheck PID/start time before any termination). Do not provide/use a blanket kill command. Preserve redacted counters and logs under `D:\Dev\BuildArtifacts\AzureIoTPyGate`; do not delete artifacts outright. Re-run the read-only process check and preflight. If ownership is uncertain, remain blocked and ask the coordinator, rather than killing a potentially live device.

### Offline tests and the isolated ten-minute soak

These are operator instructions, **not a claim that T4/tests/soak have run**. Never run a test project concurrently with another session's tests or devices. Confirm ownership/quiescence first; run one project at a time. Build/test output is redirected by `Directory.Build.props` to `D:\Dev\BuildArtifacts\AzureIoTPyGate`.

```powershell
dotnet build src\AzureIoTPyGate.slnx
dotnet test tests\AzureIoTPyGate.Tests --filter "Category!=Soak"
Get-Process pysharp -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
```

Do not infer success from a historical test-count expectation. Report the actual pass/fail/skip counts, including explicitly skipped historical live evidence. The xUnit wrapper supplies synthetic ADR/certificate fixtures and scratch where required. For direct Python tests, choose a test with no external fixture requirements, then supply fixtures for ADR/MQTT cases as the test sources specify:

```powershell
$env:PYGATE_TEST_SCRATCH = 'D:\Dev\BuildArtifacts\AzureIoTPyGate\tmp\manual-tests'
pysharp run tests\python\run_tests.py test_state_encoder_and_loader
# With the required synthetic fixtures available, no argument runs the runner's default suite:
# pysharp run tests\python\run_tests.py
Get-Process pysharp -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
```

The **600-second soak** must run **once, alone**, only after confirming no other session is testing/running devices and ordinary tests have passed:

```powershell
dotnet test tests\AzureIoTPyGate.Tests --filter "Category=Soak"
Get-Process pysharp -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
```

It launches three concurrent **loopback** agents (not Azure devices). Acceptance: zero restarts, zero payload errors and no leaked workers/agents after completion. A virtual-time unit test is not the real ten-minute soak. If any PySharp process remains, investigate ownership before proceeding; do not stop somebody else's process. Running the direct soak entry is a separate soak, so do not run it additionally by accident.

Environment names and **legacy fallbacks retained for one release**:

| Preferred | Legacy fallback / role |
|---|---|
| `PYGATE_ROOT` | Repository discovery for xUnit; `FORGE_REPO_ROOT` was renamed, not a current fallback in the copied xUnit source. |
| `PYGATE_DEVICES_ROOT` | `FORGE_CONFIG_ROOT\devices`, then `D:\Configurations\AzureIoTForge\devices` (legacy). |
| `PYGATE_PYTHON_SITE` | Installer/preflight site selector; default `D:\Configurations\AzureIoTForge\python-site` (legacy). Not an agent import variable. |
| `PYGATE_PAHO_SITE` | `FORGE_PAHO_SITE` in Python tests; set the preferred name explicitly for the xUnit wrapper. Agent uses config `pahoSite`. |
| `PYGATE_TEST_SCRATCH` | `FORGE_DEVICE_TEST_SCRATCH`; synthetic test output only. |
| `PYGATE_ADR_FIXTURE_ROOT` | `FORGE_ADR_FIXTURE_ROOT`; synthetic ADR fixture directory. |
| `PYGATE_SOAK_AGENT` | `FORGE_SOAK_AGENT`; loopback soak identity label. |
| `PYGATE_TEST_CERT` / `PYGATE_TEST_KEY` | `FORGE_TEST_CERT` / `FORGE_TEST_KEY`; paths to synthetic test certificate/key, never real credentials. |
| `PYGATE_WORKER_HEARTBEAT_TIMEOUT_SEC` | `FORGE_WORKER_HEARTBEAT_TIMEOUT_SEC`; worker watchdog override. |

### Validate these operational tools without executing them

```powershell
powershell -NoProfile -File .\ops\Validate-Ops.ps1
# Equivalent .NET 11 file-app bridge:
dotnet run --file ops/Validate-Ops.cs --artifacts-path D:\Dev\BuildArtifacts\AzureIoTPyGate\ops-validation
```

This uses `System.Management.Automation.Language.Parser` on all operational PowerShell files and preflight, then static AST/source checks against the reference, `config.py` and `dps.py`. It checks exact state/expiry keys, explicit false flags, path fields, whole-bundle hashing, UTC/epoch representation, no-clobber/missing-only guards and command boundaries. It does **not** invoke the installer, seed, preflight or launchers. `-InspectTools` optionally queries SDK/tool inventories, `pysharp --version` and `pysharp install --help` only; it still installs nothing. The bridge's explicit `--preflight` option instead runs the read-only preflight; it is not part of default source validation. Source checks are not runtime integration tests. Credential seeding, installation/copy, full preflight, four launcher guards, ordinary Python/xUnit tests and soak need separately recorded execution evidence. Keep evidence labelled **verified-offline**, **verified-live** or **not-verified**, never infer live success from syntax checks.
