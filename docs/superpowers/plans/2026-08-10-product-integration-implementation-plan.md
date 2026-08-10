# AgentState Guard Product Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the R4 packaged-Windows blocker without scope expansion, then build the next product-integration stage that turns the existing R4 core, Device Link primitives, Windows Tauri shell and Android prototype into a coherent bilingual desktop + Android product.

**Architecture:** Keep the desktop Core API on loopback `127.0.0.1:8787` as the local authority boundary. After R4 closure, expose a separate bounded Device Link Gateway on LAN port `8788`; Android establishes first trust through QR + six-digit SAS, reconnects through device-bound challenge authentication, and consumes only explicitly approved R4 projections/actions. Desktop and Android share a localized visual system and one brand identity, but Android never becomes a second authority source.

**Tech Stack:** Python 3.11+, FastAPI/Uvicorn, React + TypeScript + Vite, Tauri 2 / Rust, Kotlin + Jetpack Compose / AndroidKeyStore, ECDSA P-256, GitHub Actions, pytest, Vitest, Android emulator/instrumentation tests.

## Global Constraints

- Preserve R4/P7/P8 authority semantics; do not redesign recovery/trusted-baseline rules during product integration.
- `OFFLINE != failure`, `UNREACHABLE != SAFE`, `CHECKPOINT_CREATED != RECOVERABLE`, `R3 != TRUSTED`.
- AI output is advisory and never the source of truth.
- Core API remains loopback-only on `127.0.0.1:8787`.
- Device Link is a separate LAN-facing boundary on port `8788`; never expose the full Core API through it.
- No host Docker socket dependency.
- First trust establishment requires QR + matching six-digit SAS and explicit confirmation on both devices.
- mDNS/local discovery is convenience only and never establishes trust.
- Android private key remains in AndroidKeyStore; stale session tokens are not durable trust.
- Localization: `zh-CN`, `en-US`, follow-system. Stable machine states, IDs, hashes and reason codes remain exact.
- Approved visual direction: dark security-operations console, restrained blue/violet accents, calm normal states, prominent warning/error states, shared Shield + State Nodes identity.
- Product Integration work must not be merged into the active R4 closure candidate until R4 is formally closed.

---

## Workstream 0 — R4 Blocker Closure

### Task 0.1: Reproduce and pin the packaged Tauri API-routing defect

**Files:**
- Inspect: `web/src/api/client.ts`
- Inspect: `web/vite.config.ts`
- Inspect: `desktop/src-tauri/tauri.conf.json`
- Inspect: `desktop/src-tauri/src/sidecar.rs`
- Test: existing frontend/API client tests and Windows workflow smoke tests

**Interfaces:**
- Consumes: packaged Tauri main executable and sidecar on `127.0.0.1:8787`.
- Produces: one regression test that fails because packaged UI requests do not reach Core API.

- [ ] Capture the real Windows dogfooding evidence: main Tauri launches, sidecar reports ready, `GET /api/health` returns 200, packaged UI reports readiness/API failure, sidecar lacks expected `/api/session` and `/api/v1/runtime` requests.
- [ ] Write a failing frontend/API URL-resolution test that distinguishes Vite development behavior from packaged Tauri behavior.
- [ ] Run the focused test and record the expected failure before implementation.
- [ ] Do not change Device Link, Android, localization, visual design, R4 authority semantics or recovery code in this task.

### Task 0.2: Centralize Core API URL resolution

**Files:**
- Modify: `web/src/api/client.ts`
- Create if needed: `web/src/api/baseUrl.ts`
- Test: focused URL resolver / API client tests

**Interfaces:**
- Consumes: relative Core API paths such as `/api/session` and `/api/v1/runtime`.
- Produces: one resolver used by all desktop Core API fetches.

- [ ] Implement a single API URL resolver; packaged Tauri resolves Core requests explicitly to `http://127.0.0.1:8787` while development keeps the existing Vite proxy path.
- [ ] Ensure no component scatters new hard-coded Core base URLs.
- [ ] Keep CSP limited to the exact loopback connection already required by the desktop app.
- [ ] Run focused frontend tests.
- [ ] Commit as one atomic R4 blocker fix.

### Task 0.3: Add a packaged-main-executable Windows integration smoke

**Files:**
- Modify: `.github/workflows/desktop-windows.yml`
- Add/modify only the smallest test/launcher script needed by the workflow

**Interfaces:**
- Consumes: installed MSI and real Tauri main executable.
- Produces: CI evidence that UI bootstrap reaches Core API, not merely that sidecar health responds.

- [ ] Install the produced MSI in the CI job.
- [ ] Launch the real installed Tauri main executable.
- [ ] Prove the bundled sidecar starts.
- [ ] Prove UI bootstrap results in observable Core API session/runtime traffic or equivalent authoritative readiness evidence.
- [ ] Fail the job if only `/api/health` is reachable while UI bootstrap remains disconnected.
- [ ] Re-run Windows workflow and existing Core/CodeQL/Android workflows.

### Task 0.4: Freeze the post-fix R4 candidate

**Files:**
- Update acceptance/handoff documents only after all CI is green.

- [ ] Capture the exact new product SHA; never overwrite or relabel `cfdf9f08477b07f2999c422ba081465cee00d072`.
- [ ] Capture exact Windows artifact IDs/digests and wheel hash for the new SHA.
- [ ] Rebuild the Windows L5 handoff against the new candidate.
- [ ] Preserve explicit non-claims until L5/L4/L6 pass.

---

## Workstream 1 — R4 Acceptance Closure

### Task 1.1: Execute Windows L5 on the new candidate

- [ ] Use a real ordinary/non-elevated Windows acceptance user.
- [ ] Launch the installed main Tauri executable, not a standalone sidecar.
- [ ] Validate real runtime discovery and one controlled-change path.
- [ ] Perform `Approve Once` through the packaged UI only.
- [ ] Return the complete evidence directory with hashes and correlated IDs.
- [ ] Do not infer L4/L6/recoverability from L5.

### Task 1.2: Execute real L4 isolated runtime

- [ ] Use a genuinely restricted environment: non-root UID, zero capabilities, restricted mount namespace, no usable Docker socket, denied external network with working loopback.
- [ ] Install/use the exact-SHA wheel without editable install or network fetch.
- [ ] Capture raw environment proof plus `l4-envelope.json` as an index.

### Task 1.3: Execute formal L6 recovery drill

- [ ] Run all six required recovery cases against the exact candidate inside the real L4 envelope.
- [ ] Verify every StateDB ledger, source hash invariance and absence of illicit Trusted Baseline/production restore behavior.
- [ ] Preserve truthful interrupted `RUNNING` crash state when SIGKILL occurs.
- [ ] Close P9/R4 only after evidence review passes.

---

## Workstream 2 — Shared Product Foundation

### Task 2.1: Introduce shared localization keys on desktop

**Files:**
- Create: `web/src/i18n/*`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/AppShell.tsx`
- Modify: user-facing page/component strings under `web/src/`
- Test: localization render/unit tests

- [ ] Add `zh-CN`, `en-US`, and follow-system locale selection.
- [ ] Persist only the UI preference locally.
- [ ] Replace user-facing hard-coded English strings with stable keys.
- [ ] Preserve machine states/reason codes/IDs/hashes exactly; display localized explanation separately.
- [ ] Test language switching and default locale resolution.

### Task 2.2: Introduce Android localization resources

**Files:**
- Create/modify Android string resources for `values/` and `values-zh-rCN/`
- Modify Compose screens to use resources rather than hard-coded English labels
- Test: locale render tests where practical

- [ ] Move bottom-navigation labels and page text into Android resources.
- [ ] Support follow-system and explicit language override.
- [ ] Keep protocol/state enums exact when shown.

### Task 2.3: Implement the approved shared visual system

**Files:**
- Desktop theme/tokens under `web/src/styles/`
- Android Compose theme files

- [ ] Define semantic tokens for primary, secondary, success, warning, error, neutral and dark surfaces.
- [ ] Build shared card/button/status components rather than page-specific ad-hoc styling.
- [ ] Keep danger states visually strong without coloring all normal state as success.
- [ ] Add dark/light support only if it does not delay the primary approved dark-console implementation; follow-system can be added after the dark base is stable.

### Task 2.4: Integrate the shared Shield + State Nodes app identity

- [ ] Produce one master vector/raster source owned by the repository.
- [ ] Derive Windows `.ico`, Android adaptive icon layers and README/release artwork from the same mark.
- [ ] Remove emoji-based production branding.
- [ ] Verify recognition at 16×16 and 32×32.

---

## Workstream 3 — Device Link Product Connection

### Task 3.1: Unfreeze Device Link only after R4 closure

- [ ] Create a dedicated Product Integration branch from the closed R4 baseline.
- [ ] Preserve existing crypto/pairing semantics unless a concrete defect is proven.
- [ ] Keep Core `8787` and Gateway `8788` boundaries distinct.

### Task 3.2: Desktop Devices page and pairing session UX

**Files:**
- Add desktop Devices page/components
- Add only required bounded Device Link integration endpoints/adapter code

- [ ] Add `Devices / 设备` navigation.
- [ ] Add unpaired state and `Add mobile device` action.
- [ ] Start a bounded pairing session and produce QR payload with host, port, desktop UUID, fingerprint, session ID and expiry.
- [ ] Render QR with visible expiration timer.
- [ ] Display six-digit SAS after Android first connection.
- [ ] Require explicit desktop confirmation/rejection.
- [ ] Show bound device identity and last-seen/connection state after success.

### Task 3.3: Android Connect-to-Computer onboarding

**Files:**
- Add connection/onboarding Compose screens and ViewModel/state layer
- Reuse: `android/app/src/main/java/com/agentstate/guard/network/QrPayload.kt`
- Reuse/refactor: `DeviceLinkClient.kt`

- [ ] Replace passive `Not connected` dashboard with a first-class connection surface.
- [ ] Add `Scan QR`, `Find computers on local network`, and `Enter address manually` actions.
- [ ] QR scan creates a pairing-scoped client from the QR host/port; do not use `10.0.2.2` in production flow.
- [ ] Generate/retrieve Android device identity from AndroidKeyStore.
- [ ] Show SAS and require explicit mobile confirmation.
- [ ] Persist only durable binding metadata required for future authentication; do not persist the initial session token as durable trust.

### Task 3.4: Reconnect and endpoint discovery

- [ ] Add challenge-based reconnect using bound device identity.
- [ ] Add mDNS discovery as convenience only.
- [ ] Reject any discovered endpoint that does not match the bound desktop identity/fingerprint.
- [ ] Keep manual host/IP fallback.
- [ ] Handle endpoint/IP change without changing trust identity.
- [ ] Distinguish paired-but-unreachable from unpaired and from trust mismatch.

---

## Workstream 4 — Android Real Data Wiring

### Task 4.1: Create a single Android connection/data state layer

- [ ] Introduce ViewModel/repository state for `unpaired`, `paired-unreachable`, `authenticating`, `connected`, `degraded`, `session-expired/re-authenticating`, `protocol-incompatible`, and `trust-mismatch`.
- [ ] Screens consume typed state and never create production network clients directly.
- [ ] Add bounded retry policy; no unbounded loops.

### Task 4.2: Replace mock/default Environment state

- [ ] Remove hard-coded Claude Code/Node/Python/Docker/Tailscale sample values from production UI.
- [ ] Fetch real bounded environment/runtime projection through Device Link.
- [ ] Remove Tailscale from primary product UI unless explicitly detected as a runtime fact; it is not a product dependency.
- [ ] Preserve `UNREACHABLE` as an explicit degraded state.

### Task 4.3: Replace empty/default Changes and Checkpoints state

- [ ] Fetch real bounded changes projection.
- [ ] Fetch real checkpoint projection with truthful capability semantics.
- [ ] Do not label a checkpoint as recoverable unless authority/evidence says so.
- [ ] Add loading/offline/degraded/empty distinctions.

### Task 4.4: Wire AI Monitor to real R4 advisory state

- [ ] Show deterministic policy result separately from AI advisory.
- [ ] Show provider unavailable/configuration state without implying ALLOW.
- [ ] Keep API credentials on desktop; Android consumes projection, not provider secrets.

---

## Workstream 5 — Mobile Supervision and Bounded Actions

### Task 5.1: Add mobile supervision queue projection

- [ ] Expose only the bounded supervision fields needed by Android.
- [ ] Show session/operation identity, deterministic policy result, AI advisory, evidence refs and approval state.
- [ ] Keep mobile as a client of the same desktop authority contract.

### Task 5.2: Add mobile approval only after authority mapping review

- [ ] Map mobile `Approve Once` to the exact R4 approval authority used by desktop.
- [ ] Require explicit user action and replay protection.
- [ ] Do not introduce a parallel mobile-only approval state machine.
- [ ] Add end-to-end correlation tests proving the same session/evidence IDs appear on desktop and mobile.

### Task 5.3: Defer recovery mutations until separately reviewed

- [ ] Android may show recovery status first.
- [ ] Add recovery mutation only after an explicit authority/evidence design review and dedicated tests.

---

## Workstream 6 — AI Supervisor Coverage Expansion

### Task 6.1: Productize desktop provider/configuration UX

- [ ] Provide clear OpenAI-compatible provider configuration and connection-test UI.
- [ ] Preserve in-memory-only treatment for sensitive API credentials unless a separately reviewed secure-storage design is adopted.
- [ ] Persist only safe non-secret preferences when appropriate.

### Task 6.2: Expand supervised operation coverage incrementally

- [ ] Add operation types one at a time with deterministic local policy, authoritative evidence package, AI advisory and explicit test matrix.
- [ ] For every operation type, prove AI cannot upgrade deterministic BLOCK/UNKNOWN into ALLOW.
- [ ] Record AI assessment evidence with model/prompt/policy bindings.

---

## Workstream 7 — Independent Network/Security Audit

### Task 7.1: K3 Device Link boundary audit

- [ ] Audit LAN bind behavior and confirm Core `8787` remains loopback-only.
- [ ] Enumerate every exposed `8788` route and confirm no transparent Core proxy exists.
- [ ] Test malformed QR/host/port, expired sessions, SAS mismatch, replay, identity substitution, fingerprint mismatch, challenge replay/expiry and session expiry.
- [ ] Test mDNS spoof candidate and confirm discovery cannot establish trust.
- [ ] Confirm Android reconnect after IP change still requires the bound cryptographic identity.
- [ ] Produce an independent report; K3 must not audit network/security semantics it authored itself.

---

## Workstream 8 — Product E2E and Release Candidate

### Task 8.1: Full dual-device E2E

- [ ] Install real Windows package.
- [ ] Launch real Tauri main executable and verify Core readiness.
- [ ] Enable Device Link Gateway.
- [ ] Android scans QR; both sides confirm SAS.
- [ ] Android authenticates and receives real runtime projection.
- [ ] A controlled supervised event appears on both clients with matching IDs/evidence refs.
- [ ] Approval follows the authoritative path.
- [ ] Session expiry/re-authentication and reconnect succeed.
- [ ] External network remains unnecessary for the core dual-device flow.

### Task 8.2: Final product RC gate

- [ ] All platform CI green on one exact SHA.
- [ ] Windows real-target smoke passes.
- [ ] Android real-device pairing/data flow passes.
- [ ] Independent Device Link/network audit accepted.
- [ ] README/status/changelog accurately distinguish verified capabilities from remaining limitations.
- [ ] Produce exact artifact hashes and release notes before any stable/production claim.

---

## Agent / Branch Allocation

- **Sol/Codex disposable worker A:** R4 blocker closure only.
- **Sol/Codex disposable worker B:** Device Link product wiring after R4 closure.
- **K3 implementation role:** desktop/Android visual system, localization, UI components and non-network presentation logic.
- **K3 audit role:** independent Device Link/LAN audit only if K3 did not author the audited network/security semantics.
- **Main/long-context coordinator:** architecture decisions, task boundaries, acceptance review and merge/freeze control.
- **Durable state:** Git commits/SHA, tests, evidence, artifacts, audit reports and handoff documents — never chat/account continuity.

## Immediate Execution Order

1. R4 blocker worker fixes packaged Tauri → Core API routing and adds main-executable regression smoke.
2. Freeze new R4 candidate and run formal L5/L4/L6 closure.
3. In parallel on isolated product branches, K3 can implement localization/theme/branding UI that does not alter R4 authority semantics.
4. After R4 is closed, start Device Link Product Integration from the closed baseline.
5. Wire Android real state before adding mobile mutations.
6. Add supervision approval only after shared-authority mapping passes review.
7. Run K3 independent network audit and dual-device E2E before Product RC.
