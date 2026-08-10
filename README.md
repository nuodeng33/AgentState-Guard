# 🛡️ AgentState Guard

Local-first state guard and supervision layer for AI development environments.

> **Current status (2026-08-10): Development Preview / R4 closure in progress.**  
> The hard backend/security architecture is substantially ahead of the product shell and cross-device integration. The repository is **not yet production-ready**.

```bash
pip install agentstate-guard
agentguard doctor
agentguard ui
```

## Current Development Progress

Active R4 development branch: `feat/r4-p9-production-wiring-cxb`  
Last frozen R4 candidate before dogfooding fix: `cfdf9f08477b07f2999c422ba081465cee00d072`

Real Windows and Android dogfooding on 2026-08-10 corrected the project maturity picture: the core authority/evidence/recovery system is comparatively mature, while packaged desktop integration, Android product wiring, bilingual UI, visual design and branding still require focused product-integration work.

| Area | Current state |
|---|---|
| Runtime / agent discovery | Implemented and under R4 validation |
| Evidence Ledger / authoritative projections | Implemented; central source of evidence-backed state |
| Deterministic policy / supervision authority | Implemented; AI cannot override hard policy boundaries |
| Recovery authorization / trusted-baseline separation | Implemented at core level; formal L6 recovery drill still required |
| AI Supervisor | Authority model implemented; production coverage is still narrow |
| Controlled-change production path | Implemented for bounded operations with approval/checkpoint/verify chain |
| Windows CI / sidecar / Tauri / MSI | Build and smoke pipelines are working |
| Packaged Windows app | Dogfooding found a real Tauri UI → Core API routing blocker; fix and new candidate required before L5 |
| Device Link protocol | Crypto/pairing/gateway primitives exist and remain a separate LAN security boundary |
| Android app | APK builds/runs, but current screens are still prototype-level and largely not wired to real Device Link state |
| Desktop ↔ Android connection UX | Pairing protocol exists; visible QR/SAS connection flow is not yet productized |
| Localization | Planned: `zh-CN`, `en-US`, follow-system |
| Visual system / branding | Approved direction: dark security-operations console + blue/violet accents + Shield/State-Nodes mark |
| Public release | Not yet; no stable/production/recoverable claim |

## R4 Closure Gate

R4 remains intentionally narrow. The current closure sequence is:

```text
Fix packaged Tauri → Core API routing
        ↓
Freeze a new exact product SHA
        ↓
Windows L5 real-target validation
        ↓
L4 isolated-runtime validation
        ↓
L6 evidence-backed recovery drill
        ↓
P9 PASS / R4 COMPLETE
```

`R4 COMPLETE` will mean the R4 architecture and acceptance gates are closed. It will **not** mean the whole desktop + Android product is finished.

## Product Integration — Next Stage

After R4 closure, development moves into a dedicated product-integration stage:

- Restore Device Link development without exposing the full Core API to LAN clients.
- Keep `127.0.0.1:8787` as the desktop Core authority boundary.
- Use a separate LAN-facing Device Link Gateway on port `8788`.
- Add desktop **Devices / 设备** and Android **Connect to Computer / 连接电脑** flows.
- Use QR + six-digit SAS for first trust establishment.
- Use mDNS only for discovery convenience; discovery never establishes trust.
- Replace Android mock/default screen data with real authenticated projections.
- Add mobile supervision/evidence/recovery views through the same R4 authority contracts.
- Add shared `zh-CN` / `en-US` localization and follow-system language selection.
- Apply the approved desktop/Android visual system and a shared AgentState Guard app icon.
- Perform independent Device Link/network-boundary audit before product RC.

## Core Principles

- **Offline-first**: core operation does not depend on cloud services.
- **Evidence before claims**: `UNREACHABLE != SAFE`, `CHECKPOINT_CREATED != RECOVERABLE`.
- **AI is advisory**: AI output is never the source of truth and cannot override deterministic policy.
- **Separate authorities**: R3 is not Trusted Baseline; mobile clients are not a second authority source.
- **Fail closed**: missing evidence, ambiguous state and unavailable providers do not silently become ALLOW.
- **No host Docker socket requirement**.
- **Local Core stays local**: full Core API is not exposed directly to Android/LAN.

## Features

- **Diagnostics**: PASS/WARN/FAIL/SKIP/UNREACHABLE health checks
- **Runtime & Agent Discovery**: local execution-domain and agent discovery with degraded/unknown semantics
- **Checkpoints**: snapshot state with content-addressed blob storage
- **Evidence Ledger**: durable evidence trail for authoritative state and supervised operations
- **Supervision**: deterministic policy + bounded AI advisory + explicit approval semantics
- **Controlled Changes**: checkpoint → authorization → apply → observe → verify → terminal result
- **Recovery**: authorization-bound recovery semantics and evidence-backed drills
- **CLI**: lifecycle management commands
- **Desktop**: Tauri + React operations console
- **Android / Device Link**: secure local pairing and bounded mobile gateway primitives
- **Security**: no Docker socket requirement, path traversal protections, fail-closed authority boundaries

## Quick Start

```bash
# Install
pip install agentstate-guard

# Health check
agentguard doctor

# Create a checkpoint
agentguard checkpoint "initial state"

# Local UI
agentguard ui
```

## Security Boundaries

- ✅ Core API is designed for loopback/local authority use
- ✅ No host Docker socket required
- ✅ AI output cannot override deterministic BLOCK/UNKNOWN semantics
- ✅ Recovery/trusted-baseline authority is separate from ordinary checkpoint state
- ✅ Device Link is a separate bounded gateway rather than a transparent Core API proxy
- ✅ Path traversal and identity-binding checks are part of recovery/pairing boundaries
- ⚠️ Product-level LAN Device Link and mobile mutation coverage are still under development and audit

## Status

**v0.9.0.dev0 — Development Preview. Not production-ready.**

Current non-claims:

- R4/P9 is not yet closed.
- L5/L6 are not yet formally passed for the post-fix candidate.
- Android is not yet a complete R4 client.
- A checkpoint is not automatically a recoverability claim.
- No stable GitHub Release has been published yet.

## Development Log

### 2026-08-10 — R4 dogfooding and Product Integration design

- Built and exercised the current Windows Tauri/MSI and Android APK on real devices.
- Confirmed the Windows sidecar starts and `127.0.0.1:8787/api/health` is healthy, while the packaged Tauri frontend currently fails to reach authoritative Core API routes. This is now an R4 blocker requiring a new exact candidate after repair.
- Confirmed the Android app builds and runs, but current screens are largely prototype/default-state UI and do not expose the existing Device Link pairing flow to users.
- Approved the next-stage architecture: loopback Core `8787` + separate LAN Device Link Gateway `8788`, QR + SAS first pairing, challenge-based reconnect, mDNS discovery without implicit trust.
- Approved bilingual desktop/Android product direction (`zh-CN`, `en-US`, follow-system), shared dark security-console visual system and Shield + State Nodes app identity.
- Product Integration is intentionally separated from R4 closure so UI/mobile expansion does not invalidate acceptance boundaries.

## License

Apache 2.0
