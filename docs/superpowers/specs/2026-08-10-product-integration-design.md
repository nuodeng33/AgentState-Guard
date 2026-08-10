# AgentState Guard Product Integration Design

Date: 2026-08-10
Status: DESIGN FOR USER REVIEW
Base candidate: `cfdf9f08477b07f2999c422ba081465cee00d072`
Design branch: `docs/product-integration-design`

## 1. Purpose

This design defines the product-integration stage that follows R4 closure. It turns the already substantial R4 core, Device Link security primitives, Windows packaging, and Android shell into one coherent desktop + Android product.

The design intentionally separates two concerns:

1. **R4 closure remains narrow.** Fix the packaged Windows UI-to-Core API routing blocker, freeze a new candidate, complete L5/L4/L6, then close R4.
2. **Product Integration begins after R4 closure.** Re-enable Device Link development, connect real Android state to the R4 authority model, add the missing pairing UX, bilingual UI, shared visual system, app branding, and broader AI/mobile supervision UX.

`R4 COMPLETE` must not be used as a synonym for `AgentState Guard product complete`.

## 2. Current-state correction

Dogfooding on a real Windows installation and a real Android device exposed an important maturity split.

### 2.1 Mature or relatively mature layers

- Runtime and agent discovery.
- Evidence Ledger and authoritative projections.
- Deterministic policy and supervision authority.
- Recovery authorization semantics and P7/P8 safety boundaries.
- AI Supervisor authority model: AI advisory is not source of truth.
- Controlled-change production path.
- Windows CI, sidecar build, Tauri build, MSI packaging, and smoke infrastructure.
- Device Link cryptography, pairing state machine, registry/session concepts, and read-only gateway primitives.

### 2.2 Product-integration gaps exposed by dogfooding

- Packaged Tauri frontend currently fails to reach the loopback Core API even while `127.0.0.1:8787/api/health` is healthy.
- Android has no visible "connect computer" / pairing flow.
- Android `DeviceLinkClient` exists but is not wired into the current screens.
- Android screens currently contain default/mock state, including environment values and empty change/checkpoint collections.
- Android defaults to emulator-only endpoint `http://10.0.2.2:8788` rather than a real paired desktop endpoint.
- Desktop and Android are English-only.
- Desktop and Android visual design is still engineering-console / prototype quality.
- Branding and app icon are not yet treated as a shared product system.
- Mobile supervision/approval/evidence/recovery UX is not yet aligned with R4.

The conclusion is: **the hard backend/security architecture is substantially ahead of the product shell and integration layer.**

## 3. Scope

### 3.1 In scope

- Fix packaged Tauri → Core API routing before R4 closure.
- Preserve `8787` as loopback-only Core authority boundary.
- Re-enable Device Link after R4 closure.
- Preserve Device Link as a separate LAN-facing security boundary on `8788`.
- Desktop Devices page and pairing flow.
- Android Connect-to-Computer flow.
- QR + SAS first trust establishment.
- mDNS/local discovery only as convenience, never as trust authority.
- Challenge-based reconnect using device identity.
- Real Android data wiring for environment, checkpoints, changes, AI status, and later supervision/evidence projections.
- Shared zh-CN/en-US localization system.
- Shared visual design system based on the approved dark security-console direction.
- Shared AgentState Guard app icon / brand mark.
- Desktop and Android end-to-end tests for connection and authoritative projections.
- Clear agent work separation so implementation and network audit remain independently reviewable.

### 3.2 Explicitly out of scope for this stage

- Cloud relay/account infrastructure.
- Internet-required pairing.
- Tailscale as a product dependency.
- Exposing the full Core API over LAN.
- Host Docker socket dependency.
- Android becoming a second authority source.
- AI being allowed to override deterministic policy.
- Silent trust establishment from mDNS discovery alone.
- Rewriting the Device Link cryptographic protocol without a concrete defect.
- Large unrelated R4 semantic changes.

## 4. Architectural decision

Use a **two-boundary desktop architecture**.

```text
Desktop Tauri UI
      |
      | loopback only
      v
Core API / Authority
127.0.0.1:8787
      |
      | explicit projection / bounded actions
      v
Device Link Gateway
LAN interface :8788
      |
      | ASDL/1 authenticated local link
      v
Android App
```

### 4.1 Core API — port 8787

Properties:

- Bind to loopback only.
- Source of authoritative local desktop state and actions.
- Session token remains local/in-memory according to R4 UI rules.
- Not directly exposed to Android or the LAN.
- Packaged Tauri frontend must resolve API requests explicitly to the loopback Core API instead of depending on Vite development proxy behavior.

### 4.2 Device Link Gateway — port 8788

Properties:

- Separate security boundary from Core API.
- LAN-facing only when Device Link is enabled.
- Exposes only explicitly approved mobile projections/actions.
- Never becomes a transparent proxy to the full Core API.
- Authentication remains device-bound and session-based.
- Initial product permission profile remains minimal; new mutation capabilities are added one by one with explicit authority mapping.

This preserves the existing design principle already documented in `agentguard/device_link/gateway.py`: the mobile gateway must not expose the full Core API.

## 5. First-time connection flow

### 5.1 Desktop UX

Add a top-level **Devices / 设备** page.

Unpaired state:

```text
Mobile Devices
No phone is connected.

[ Add mobile device ]
```

Selecting Add Mobile Device:

1. Start a bounded Device Link pairing session.
2. Determine a usable LAN endpoint for the gateway.
3. Generate a QR payload containing:
   - host
   - port (`8788`)
   - desktop UUID
   - desktop key/fingerprint information required by protocol
   - pairing session ID
   - expiry
4. Display QR code and expiration timer.
5. Wait for Android first connection.
6. Display the six-digit SAS.
7. Require explicit desktop confirmation.
8. On both-side confirmation, show bound Android device identity and connection state.

### 5.2 Android UX

When no desktop is paired, Home is not a passive status dashboard. It becomes an onboarding/connection surface:

```text
AgentState Guard
Protect your AI development environment.

No computer connected

[ Scan QR code ]
[ Find computers on local network ]
[ Enter address manually ]

First connection requires matching the same 6-digit security code on both devices.
```

Primary path: **Scan QR code**.

Flow:

1. Camera scans `agentstate://pair?...` payload.
2. Parse QR using the existing `QrPayload` contract.
3. Create a pairing-scoped `DeviceLinkClient` using the QR host/port; do not use the emulator default.
4. Android device identity is generated/retrieved from AndroidKeyStore.
5. Execute the existing pairing state machine.
6. Show six-digit SAS prominently.
7. User confirms or rejects.
8. On successful pairing, persist only durable binding metadata required for future authentication; do not treat the initial session token as a durable credential.
9. Return to connected Home dashboard.

### 5.3 SAS rules

- The code must be shown on both devices.
- Neither side may auto-confirm.
- Mismatch or timeout terminates the pairing attempt.
- Pairing sessions remain bounded and expiring.
- Repeated attempts use the existing attempt/expiry protections.

## 6. Reconnection and discovery

### 6.1 Durable trust

Durable trust is based on bound device identity and cryptographic proof, not IP address.

Persisted Android-side metadata may include:

- desktop UUID
- trusted desktop fingerprint
- display name
- last-known endpoint
- protocol version

The Android private key remains in AndroidKeyStore.

### 6.2 Reconnect

On app start or foreground:

1. Discover or resolve a candidate endpoint.
2. Verify it corresponds to the bound desktop identity.
3. Request a fresh challenge.
4. Sign the challenge with the Android device key.
5. Receive a fresh bounded session token.
6. Fetch allowed projections.

A stale session token must not be treated as durable trust.

### 6.3 mDNS

mDNS is a convenience feature only.

It may advertise/discover:

- AgentState Guard service presence
- device display name
- LAN endpoint
- protocol version

It must **not** establish trust. A discovered machine that is not already bound still requires QR/SAS pairing.

### 6.4 Manual address fallback

Provide a manual host/IP entry for networks where multicast discovery fails.

Manual entry changes reachability only, not trust requirements.

## 7. Mobile authorization model

Initial Device Link remains minimal and projection-oriented.

### 7.1 Read projections

Android should eventually receive real, bounded projections for:

- overall health
- runtime/environment
- detected agents
- changes
- checkpoints and their truthful capability state
- AI Supervisor status/advisory
- supervision queue
- evidence references
- recovery status

### 7.2 Mobile mutations

Do not immediately expose every desktop action.

Mutation rollout order:

1. Pair / unpair device.
2. Explicit supervision approval where the R4 authority contract allows it.
3. Explicitly bounded recovery actions only after their authority and evidence semantics are mapped and tested.

Every mobile mutation must map to the same authoritative R4 contract used by desktop. Android never creates a parallel authority path.

## 8. Android data wiring

Current Compose screens must stop constructing production UI from default/mock values.

Introduce a single state/data layer responsible for:

- connection lifecycle
- bound desktop identity
- fresh session authentication
- fetch/retry policy
- projection decoding
- offline/degraded state
- ViewModel/UI state mapping

Screens consume state; they do not own network clients directly.

Required states must distinguish at least:

- unpaired
- paired but unreachable
- authenticating
- connected
- degraded/partial
- session expired/re-authenticating
- protocol incompatibility
- trust/fingerprint mismatch

`UNREACHABLE` must not be rendered as `SAFE` or `OK`.

## 9. Desktop packaged API routing fix

Before Product Integration starts, R4 closure must repair the real Windows blocker found by dogfooding.

Observed condition:

- Tauri main app launches.
- Sidecar starts and reports ready.
- `127.0.0.1:8787` is listening.
- `GET /api/health` returns `200`.
- Packaged UI reports API/readiness failure.
- Sidecar does not receive the UI's expected `/api/session` / authoritative-view requests.

Design requirement:

- Centralize API URL resolution in the frontend client.
- Development may continue to use Vite proxy behavior.
- Packaged Tauri must explicitly target the loopback Core API.
- Do not scatter hard-coded base URLs through components.
- CSP must continue to permit the exact loopback connection and no broader network access than necessary.

Regression tests:

- frontend API resolver unit test
- packaged Windows integration smoke that launches the real main Tauri EXE and proves the UI bootstrap reaches the Core API

A new product SHA and new acceptance artifacts are required after this fix.

## 10. Information architecture

### 10.1 Desktop

Recommended primary navigation:

- Home / 首页
- Runtime / 运行环境
- Agents / 智能体
- Supervision / 监管
- Changes / 变更
- Recovery / 恢复
- Devices / 设备
- AI Monitor / AI 监管
- Settings / 设置

Evidence is surfaced contextually from supervision/change/recovery detail views; a separate Evidence page may be added only if navigation testing proves it necessary.

### 10.2 Android

Primary bottom navigation should remain small enough for mobile.

Recommended bottom tabs:

- Home / 首页
- Environment / 环境
- Changes / 变更
- Supervision / 监管
- More / 更多

`More` contains:

- Checkpoints
- Recovery
- AI Monitor
- Devices / connection management
- Settings

This avoids overloading the bottom bar as product capability expands.

## 11. Visual design system

Approved direction: **dark security operations console with restrained blue-violet accenting**.

### 11.1 Principles

- Security/operations feel, not generic chatbot styling.
- High information density without visual noise.
- Consistent desktop/mobile component language.
- Clear hierarchy for authority and evidence.
- Normal states are calm; danger states are visually strong but not omnipresent.
- No decorative animation that hides state transitions.

### 11.2 Core tokens

Use a small token set shared conceptually across React/Tauri and Compose:

- primary blue
- secondary violet
- success green
- warning amber
- error red
- neutral slate
- dark background / elevated surface / tertiary surface

Exact color values may be tuned during implementation, but status meaning must remain stable.

### 11.3 Status semantics

Visual labels should preserve machine state while adding localized explanation.

Examples:

```text
需要审核
REVIEW

不可访问
UNREACHABLE
```

Do not translate away stable reason codes, IDs, hashes, policy enums, evidence IDs, or protocol values.

## 12. Localization

Support:

- Follow system / 跟随系统
- 简体中文 (`zh-CN`)
- English (`en-US`)

Rules:

- Chinese system locale defaults to Chinese; otherwise English.
- User override is persisted locally as a UI preference.
- Desktop and Android use stable string keys, not hard-coded page labels.
- User-facing prose is translated.
- Machine states, IDs, hashes, evidence references, reason codes, and protocol constants remain exact.
- Mixed bilingual labels on every button are avoided; the UI uses one selected language at a time.

## 13. Brand and app icon

Approved concept: **Shield + State Nodes**.

Meaning:

- shield = protection / bounded authority
- node/trace = agent state and evidence continuity
- central highlighted node = current authoritative state
- path/branches = before/current/recovery relationship

Requirements:

- recognizable at 16×16 and 32×32
- no text inside icon
- no emoji-based production branding
- works on dark and light surfaces
- same master mark feeds Windows `.ico`, Android adaptive icon, README, release artwork, and future promotional material

The visual mockup generated on 2026-08-10 is the design-direction reference, not itself a final production asset specification.

## 14. AI Supervisor product integration

Do not redesign the AI authority model.

Product work should instead:

- expose provider/configuration UX cleanly
- preserve memory-only handling for sensitive API credentials unless a separately reviewed secure storage design is adopted
- show deterministic policy result separately from AI advisory
- show when AI is unavailable without turning that state into ALLOW
- expand supervised operation coverage only through bounded, explicitly tested operation types
- expose appropriate AI advisory/projection to Android after Device Link wiring is complete

The UI must communicate that AI is an adviser inside the supervision chain, not the source of truth.

## 15. Testing strategy

### 15.1 Desktop

- API resolver unit tests.
- Tauri packaged UI → Core API integration smoke.
- existing sidecar/MSI/runtime tests retained.
- localization render tests for critical pages.
- Devices pairing UI state tests.

### 15.2 Device Link

- QR parsing and expiry.
- invalid host/port and malformed payload rejection.
- SAS mismatch/timeout/replay cases.
- device identity mismatch.
- challenge expiry and replay.
- session expiry and re-authentication.
- LAN endpoint change with same trusted identity.
- mDNS spoof candidate does not bypass trust.
- gateway exposes only approved routes.
- Core-only routes remain unreachable through Device Link boundary.

### 15.3 Android

- ViewModel/state tests without network.
- emulator tests for unpaired → paired → reconnect flows.
- real-device LAN pairing test.
- real projection test against current desktop build.
- offline/reachable transition tests.
- language switching and system-locale tests.

### 15.4 End-to-end

Formal product E2E must include:

```text
Windows install
→ launch real Tauri main executable
→ Core healthy
→ Device Link enabled
→ Android scans QR
→ both confirm SAS
→ Android authenticates
→ Android receives real runtime projection
→ a controlled supervised event appears on both clients
→ authority/evidence IDs correlate
→ reconnect succeeds after session expiry
```

## 16. Workstream separation and agent roles

Cheap/disposable agent accounts can be used aggressively, but durable state remains Git commits, tests, evidence, artifacts, and handoff documents.

Recommended role split:

### Workstream A — R4 blocker closure

- Strong coding agent (Sol/Codex class): fix packaged API routing and add regression coverage.
- Independent review agent: verify no expansion into Device Link/product redesign before R4 closure.

### Workstream B — Visual/i18n/frontend shell

- K3 may implement desktop/Android visual-system and frontend work.
- This work should avoid changing Device Link security semantics unless explicitly assigned.

### Workstream C — Device Link implementation

- Strong coding agent: desktop Devices flow, Android pairing orchestration, real state wiring, reconnect, mDNS convenience layer.

### Workstream D — Network/security audit

- K3 remains the designated network-audit specialist.
- For audit independence, K3 must **not audit network-security changes it authored itself**.
- If K3 wrote UI-only code, it may still independently audit Device Link/network code written by another agent.
- If K3 touches Device Link/network semantics, network audit must move to a separate independent reviewer/session with no self-review claim.

This keeps the useful specialization without collapsing implementation and assurance into the same actor.

## 17. Delivery sequence

### Phase 0 — R4 closure blocker

1. Keep `cfdf9f0` immutable as historical RC.
2. Fix packaged Tauri API routing.
3. Add packaged-main-app regression smoke.
4. Freeze new SHA.
5. Regenerate exact-SHA artifact/handoff hashes.
6. Run L5, L4, L6.
7. Close P9/R4 only when evidence permits.

### Phase 1 — Product foundation

1. Establish shared design tokens.
2. Implement localization foundation.
3. Implement final app icon asset pipeline.
4. Refactor Android mock/default screen state behind real state interfaces without yet widening authority.

### Phase 2 — Device Link UX

1. Desktop Devices page.
2. Android connect/onboarding page.
3. QR scanner and manual fallback.
4. Wire existing pairing state machine.
5. SAS confirmation on both devices.
6. Durable binding + fresh-session reconnect.

### Phase 3 — Real mobile projections

1. Environment/runtime.
2. Agents.
3. Changes.
4. Checkpoints with truthful capability states.
5. AI Supervisor/advisory.
6. Evidence references.

### Phase 4 — Mobile supervision

1. Supervision queue projection.
2. Explicit mobile approval using the same authority contract.
3. Cross-device evidence correlation.
4. No recovery mutation until separately gated.

### Phase 5 — Product RC

1. Desktop/Android visual polish complete.
2. zh-CN/en-US complete for primary flows.
3. real Windows + real Android E2E.
4. independent network audit.
5. release docs/screenshots/install guidance.
6. new Product RC freeze.

## 18. Acceptance criteria

Product Integration is not complete until all of the following are true:

- Real packaged Windows app reaches Core API without development proxy assumptions.
- Android has an obvious connection flow.
- Real phone can pair to real Windows desktop over LAN.
- QR/SAS trust establishment works and rejects mismatch/replay/expiry.
- Android no longer displays production mock environment/checkpoint/change/AI state.
- Android reconnects using durable cryptographic identity and a fresh session.
- Device Link gateway does not expose full Core API.
- Desktop and Android support zh-CN/en-US and system-language selection.
- Shared icon/branding is present in both installable applications.
- Shared visual system is applied to primary flows.
- Mobile supervision uses the same R4 authority semantics rather than a parallel approval path.
- Network audit is independently performed on the final Device Link surface.
- A real-device cross-platform E2E run produces durable evidence.

## 19. Non-normative maturity assessment

The following is a planning aid, not an acceptance metric:

- R4 backend/security architecture: high maturity.
- Windows packaging/build chain: high maturity, with a real packaged integration blocker identified by dogfooding.
- Desktop product UX: medium maturity.
- Device Link protocol/security primitives: medium-to-high maturity.
- Android product wiring: low maturity despite a working APK and network primitives.
- Android UX/branding/localization: prototype maturity.
- Full dual-end product: materially less complete than the backend core.

The principal lesson from dogfooding is that CI and architectural completeness did not prove the final product integration layer. Real installed applications exposed issues that unit/integration build pipelines did not.

## 20. Final design decisions

The following are frozen by this design unless a concrete defect forces reconsideration:

1. `8787` remains loopback Core authority.
2. `8788` remains separate Device Link LAN gateway.
3. Android never directly receives unrestricted Core API access.
4. QR + SAS is the first-trust path.
5. mDNS/manual-IP are discovery/reachability aids, not trust mechanisms.
6. Durable trust is cryptographic device identity, not IP/session token.
7. Android production UI must use real state, not default mock values.
8. zh-CN + en-US + follow-system is the localization baseline.
9. Shield + State Nodes is the app-icon/brand direction.
10. Approved visual direction is the restrained dark security-console design shared across desktop and Android.
11. AI remains advisory under deterministic authority.
12. Network implementation and final network audit must remain independently reviewable.
