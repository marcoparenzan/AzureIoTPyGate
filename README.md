# AzureIoTPyGate

A gateway is an independently runnable device implementation managed by AzureIoTForge. PyGate is a simulated Python device hosted by PySharp: a stable agent provides DPS identity, verified TLS MQTT, twins, direct methods and signed OTA; a replaceable thermostat payload supplies measurements.

Start with [installation and operation](USAGE.md). See [architecture and historical contract evidence](ARCHITECTURE.md), [roadmap and open decisions](ROADMAP.md), and [project rules](AGENTS.md). The original bootstrap specification remains in docs/AGENT-INSTRUCTIONS.md.

## Build and offline verification

```powershell
dotnet build src/AzureIoTPyGate.slnx
dotnet test tests/AzureIoTPyGate.Tests --filter "Category!=Soak"
.\scripts\Preflight.ps1
```

This migration is **partial**, not fully accepted. The recorded 2026-11-12 non-soak check passed 30 cases, with 0 failures and 0 skips (verified-offline; D:\Dev\BuildArtifacts\AzureIoTPyGate\logs\check-test.log). InspectSyntheticLogsWithoutSecrets checks synthetic count-only logs, not live reliability. Historical round 1 evidence (recorded 2026-11-12): 29 passed, 1 skipped; an intermediate heartbeat-idle run failed and a rerun passed. The isolated soak and full launcher guard matrix have not been verified. Preflight currently reports external device packages as blocked; do not start them until blockers are resolved and the owner approves that specific start. No Azure calls or live device starts were made.

The project targets only net11.0, with the solution under src and documentation at the root. PySharp remains the existing separately installed 2.5.1-net11-preview.10 tool. Generated output is external under D:\Dev\BuildArtifacts\AzureIoTPyGate.

## Device contract

Forge owns the formats: resolve the sibling through PYGATE_FORGE_ROOT or ../AzureIoTForge, and read docs/DEVICE-CONTRACT.md when available, otherwise ARCHITECTURE sections 2–6. PyGate implements X509Dps (MQTT) and X509DpsAdr (HTTPS CSR/issued certificate); the overall contract also describes Direct identities, which must not be assumed fully implemented by this bootstrap.

Hub MQTT uses port 8883 and TLS server verification. Telemetry JSON has ts, deviceId, temperature, setpoint and payloadVersion, with alarmState, encoding and schema properties. Desired/reported twins, direct methods and payload manifests remain unchanged. Parquet batching is not implemented. OTA signatures use ECDSA P-384 / SHA-384, IEEE P1363 hex over exact script bytes; only the public signing key belongs on the device. Probation, watchdog and last-known-good rollback protect payload updates.

Credential folders contain dps-registration.json, issued-chain.pem, private keys, bootstrap certificates and device.csr.pem; PyGate's offline seed tool creates missing agent-config.json, agent-state.json and issued-chain.expiry.json. Keys remain outside the repository. Any format change is a coordinated cross-project owner decision.

Original files remain in AzureIoTForge; **they should not yet be removed** based on this incomplete acceptance.

MIT License — Copyright (c) 2026 Marco Parenzan.
