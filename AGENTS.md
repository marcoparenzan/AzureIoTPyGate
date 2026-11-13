# Project rules

Code and documentation are English; communicate with the owner in Italian. MIT: Copyright (c) 2026 Marco Parenzan. Never store or print credentials, keys, SAS tokens or connection strings.

This session requires net11.0, as required by round 2 and the developer session rules. Keep nullable and implicit usings enabled, exact central package versions, and warnings as errors. Solution files live in src; documentation lives at the repository root.

All generated output, logs, scratch and reports belong in D:\Dev\BuildArtifacts\AzureIoTPyGate, not this repository. Never delete artifacts outright: move them to D:\Dev\BuildArtifacts\trash and tell the owner.

Do not modify sibling repositories. Resolve sibling paths relative to this repository with environment overrides; never hard-code them. AzureIoTForge owns the device contract. Do not change wire formats unilaterally.

Do not publish, install or update global tools, commit, alter Azure resources, register with DPS, or start a real device without explicit permission for that action. Never enable allowDpsRegistration. Read-only Azure queries are allowed. Do not stop processes you did not start. Run test projects serially; run soak alone only after confirming no other session is testing or running devices.

Report evidence faithfully as verified-live, verified-offline or not-verified. Never claim an unopened UI works.

## PySharp boundary

PySharp is separate. Changes require approval and a genuine missing feature or bug needed by the device; keep changes minimal, generic and free of IoT-specific code. Follow its conventions, tests and README, USAGE, ARCHITECTURE, ROADMAP and RELEASE_NOTES. Pack only to D:\Dev\NuGetLocalFeed with a new -net11-preview.n version; never publish or update the global tool without asking. Record every changed PySharp file in the PySharp changes section of the root decision log (ROADMAP.md in this session). Otherwise record gaps with a minimal reproducer and work around them in the agent. PySharp ignores PYTHONPATH and does not set __file__ for entry scripts.
