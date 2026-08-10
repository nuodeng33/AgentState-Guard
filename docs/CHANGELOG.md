# Changelog

## 2026-08-10 — R4 dogfooding and Product Integration design

### R4 / Windows
- Exercised the current Windows Tauri/MSI build on a real Windows target.
- Confirmed the bundled sidecar starts and `127.0.0.1:8787/api/health` returns 200.
- Found a packaged Tauri frontend → Core API routing blocker: the UI reports readiness/API failure while the Core sidecar is healthy.
- Result: `cfdf9f08477b07f2999c422ba081465cee00d072` remains a historical frozen candidate, but a new exact product SHA is required after the routing fix before formal L5.

### R4 Core status
- Runtime/agent discovery, Evidence Ledger, deterministic policy, supervision authority, recovery authorization semantics and bounded controlled-change flow are substantially implemented.
- AI remains advisory and cannot override deterministic BLOCK/UNKNOWN authority.
- Formal L5/L4/L6 acceptance remains required before `P9 PASS` / `R4 COMPLETE`.

### Android / Device Link
- Installed and exercised the current Android APK on a real device.
- Confirmed the current Android app is prototype-level: visible pairing/connection UX is missing and several screens still rely on default/mock state.
- Existing Device Link crypto/pairing/gateway/client primitives remain the basis for the next stage rather than being rewritten.
- Approved product connection architecture: loopback Core `8787` + separate LAN Device Link Gateway `8788`, QR + six-digit SAS first trust establishment, challenge-based reconnect, mDNS discovery without implicit trust.

### Product Integration design
- Approved bilingual desktop/Android UI: `zh-CN`, `en-US`, follow-system.
- Approved shared dark security-operations visual direction with restrained blue/violet accents.
- Approved shared Shield + State Nodes application identity for Windows, Android, README and release assets.
- Product Integration is intentionally separated from R4 closure so UI/mobile expansion does not invalidate R4 acceptance boundaries.

## 2026-07-23

- Initialized AgentState Guard v0.1.0
