# R4-P9 Preflight Gap Audit

Audit base: `0b791d7e8abe3a503a22a99f53fc03bc624d8b07`

Old contract input: `6f646045267cf9573b4fed0d77e2c08ca09bc134`

Branch: `feat/r4-p9-alignment`

Audit mode: direct source/test/workflow inspection of the P8 frozen codebase.
No product defect was fixed. Test definitions were inspected but are not
reported as newly executed results. P8 tribunal results remain valid only for
the P8 frozen SHA and do not satisfy a future P9 final-SHA gate.

## Alignment conclusions

1. The R4 ladder is `L1 Static`, `L2 Unit`, `L3 Integration / CI`,
   `L4 Isolated Runtime`, `L5 Real Target`, `L6 Recovery Drill`.
2. Old local artifact/hash/performance references were not renamed to another
   level. They are observation/provenance requirements.
3. ACC-017 is now classified as L3 build/CI plus L4 offline installation.
4. ACC-019 is an observation envelope, with L5 required only for a real-target
   performance claim.
5. ACC-021 is a claim-to-evidence audit; every mapped claim still needs its own
   evidence level.
6. ACC-001..022 objective acceptance semantics are otherwise unchanged.
7. P8 prerequisites changed from blocked to frozen/pass at the P8 frozen SHA.
   The future P9 integration SHA remains unbound.

## Code-grounded findings

### GAP-001 — Discovery observations have no product ingestion path

- `agentguard/evidence/discovery_adapter.py` implements `discovery_events()`.
- Repository references outside its definition occur only in tests.
- Runtime and agent API projection can read verified Ledger events, but current
  CLI/API product flow does not append discovery snapshots to the Ledger.
- `EvidenceEvent.execution_domain_id` is left `None` by the adapter; the read
  projection falls back to a sanitized value inside payload data.

Impact: ACC-005, ACC-006, ACC-016, and ACC-022 are not executable as a
correlated product path.

### GAP-002 — Workspace authority is declared but not produced

- `EventType.WORKSPACE_LINKED` exists.
- No production module emits it.
- `discovery_events()` allowlists runtime, agent, and unreachable facts only.
- `R4ReadProjectionService.agents()` always emits
  `workspace: {status: UNKNOWN, binding_ref: null}`.
- Tests explicitly verify that discovery projection emits no workspace link.

Impact: ACC-007 is MISSING and all mutation requirements that depend on a
server-owned workspace/scope binding remain blocked.

### GAP-003 — R4 AI supervisor is not wired into a product route

- `AISupervisor` constrains BLOCK/UNKNOWN/REVIEW and has fail-closed provider
  behavior in unit tests.
- No CLI or API production path constructs `AISupervisor`.
- Existing legacy AI API endpoints are not the R4 authoritative supervisor.

Impact: ACC-009 has component logic but no product offline/bypass execution.

### GAP-004 — Required checkpoint cannot satisfy activation

- `SupervisionService.activate(session_id, checkpoint_id)` accepts a
  `checkpoint_id` argument but does not read or validate it.
- Any row with `requires_checkpoint = 1` remains non-active.
- The named checkpoint test proves only that activation is blocked; it does not
  prove a valid checkpoint can authorize activation.
- Snapshot V3/recovery coverage exists separately, but there is no durable
  session/workspace/scope/checkpoint activation binding.

Impact: ACC-011 is MISSING; ACC-012..014 and the full chain cannot begin.

### GAP-005 — Legacy transaction engine is not an R4 controlled-change path

- `TransactionEngine` writes legacy audit events, not R4 Evidence Ledger
  change events.
- It has no binding to Supervision session, policy event, action binding,
  workspace, scope digest, or exact SHA.
- `create_plan()` accepts caller file paths and creates a legacy checkpoint.
- `apply()` only transitions the transaction row to `applying`; it executes no
  change.
- `verify()` checks readable file hashes and commits state, without comparing
  approved before/after scope or running an immutable verification plan.
- CHANGE event types such as `DECLARED_SCOPE`, `SCOPE_DRIFT`, and
  `EXTERNAL_EFFECT_UNKNOWN` are declared but not emitted by product code.

Impact: ACC-012, ACC-013, and ACC-014 are MISSING; ACC-016 is incomplete.

### GAP-006 — Diff and verification utilities are not authority-bound

- Snapshot/diff utilities and legacy CLI commands have unit/integration tests.
- No result binds the diff to one R4 session, workspace, checkpoint, controlled
  change, and approved scope.
- CLI `verify` checks database integrity/migrations.
- No post-diff offline verifier records a frozen command plan and results.

Impact: utilities cannot be promoted into ACC-013/014 acceptance.

### GAP-007 — Recovery service is strong, but the packaged path and L6 record are absent

- RecoveryService, Snapshot V3, R2 test-restore, R3 drill, Trusted Baseline,
  Ledger fault handling, and adversarial tests are substantial.
- Service tests use actual temporary files and isolated destinations, which is
  stronger than pure mocks but is still not an L4/L6 evidence envelope.
- CLI `test-restore` only lists restorable files from a legacy snapshot; it
  does not invoke `RecoveryService.test_restore()`.
- No packaged correlated run retains sandbox identity, injected faults,
  before/after hashes, network/permission proof, and cleanup evidence.

Impact: ACC-015/016 are PARTIAL; checkpoint existence still does not prove
recoverability, and R3 still does not prove TRUSTED.

### GAP-008 — No durable L4 restricted/offline/hostile harness

- Unit tests model network prohibition, access denial, malformed inputs,
  traversal, concurrency, and atomic failures.
- There is no one packaged runner that proves external network denial,
  non-root identity, restricted modes, missing Docker socket/capabilities,
  dirty workspace preservation, and the complete product result.

Impact: ACC-001..004 and ACC-022 cannot receive L4 evidence. A mocked
`PermissionError` remains L2 only.

### GAP-009 — Current Windows workflow is L3 and remains sidecar-centric

- `desktop-windows.yml` builds the Tauri executable/MSI and succeeds in CI.
- `runtime-smoke` directly starts the bundled sidecar and calls health/session/
  readiness APIs; it does not launch the main Tauri executable.
- `msi-smoke` installs and locates the main executable and sidecar, then
  uninstalls; it never launches the installed main application.
- A GitHub-hosted Windows runner is L3 even if those gaps are later closed.

Impact: ACC-018 and the L5 portions of ACC-002/003/005/006/022 remain BLOCKED
on a real standard-user Windows host and a main-Tauri procedure.

### GAP-010 — Wheel build exists; auditable offline provenance does not

- Core CI rebuilds web assets, builds a wheel, installs it non-editably, and
  uploads the wheel.
- The workflow does not record the wheel SHA-256 or prove local/CI artifact
  identity.
- Installation may fetch runtime dependencies and therefore is not an offline
  L4 install.

Impact: ACC-017 is PARTIAL, not PASS.

### GAP-011 — Existing performance and release prose lacks acceptance provenance

- `docs/PERFORMANCE_REPORT.md` contains values but no exact run command,
  timestamps, sample series, raw artifact, SHA-256, or final-SHA envelope.
- `docs/FINAL_VERIFICATION.md` explicitly describes an older branch/HEAD and
  historical counts; inheriting the file at a newer SHA does not re-run it.
- No executable claim mapper ties release sentences to ACC/gate/evidence IDs.

Impact: ACC-019 and ACC-021 are MISSING. No SLA or performance PASS/FAIL may
be inferred from the prose.

### GAP-012 — No single full-chain correlation exists

The codebase contains many strong components, but there is no durable
correlation across discovery ingestion, workspace binding, deterministic
policy, optional AI, approval, checkpoint authorization, controlled change,
post-change diff, offline verification, isolated restore, and final Ledger
verification.

Impact: ACC-022 is BLOCKED. Separate tests and historical runs cannot be
spliced into the result.

## Top blockers

1. Missing product Discovery→Ledger→workspace authority path.
2. Missing checkpoint-to-session/workspace/scope activation binding.
3. Missing R4 controlled-change, bound diff, and offline verification path.
4. Missing reusable correlated L4 harness and retained evidence envelope.
5. Missing L5 main-Tauri standard-user validation on a real Windows target.
6. Future P9 final integration SHA and its exact-SHA artifacts do not yet exist.

## Ordered P9 execution slices

Slices are sequential at authority boundaries. No two executors may modify the
same authority subsystem concurrently. Terra is not a default executor.

### Slice 1 — Authoritative discovery and workspace binding

- Executor: CX.
- Depends on: this frozen alignment revision only.
- May modify: `agentguard/discovery/**`,
  `agentguard/evidence/discovery_adapter.py`,
  `agentguard/api/r4_projection.py`, narrowly required StateDB migration/schema
  code, and focused discovery/workspace tests.
- Must not modify: supervision, transaction, recovery trust, frontend, or
  packaging subsystems.
- Covers: ACC-005, ACC-006, ACC-007, partial ACC-016/022.
- Deliverable: product ingestion appends bounded domain-bearing discovery and
  workspace authority; missing/conflicting bindings deterministically prevent
  mutation.

### Slice 2 — Policy, AI fallback, approval, and checkpoint activation binding

- Executor: CX.
- Depends on: Slice 1 stable workspace/domain interface.
- May modify: `agentguard/policy/**`, `agentguard/ai/supervisor.py`,
  `agentguard/supervision/**`, narrowly required recovery-coverage interfaces,
  CLI/API authority routes, and focused tests.
- Must not modify: transaction execution internals or P7 recovery authorization
  semantics.
- Covers: ACC-008, ACC-009, ACC-010, ACC-011, partial ACC-016/022.
- Deliverable: deterministic policy and optional AI run on server authority;
  one approved session can activate only with a valid bound checkpoint while
  preserving P8 and P7 semantics.

### Slice 3 — Controlled change, post-change diff, and offline verification

- Executor: CX.
- Depends on: Slice 2 frozen activation/binding interface.
- May modify: `agentguard/transactions/**`, a narrowly scoped R4 change service,
  CHANGE-family evidence production, diff/verification command services, DB
  schema needed for binding, and focused tests.
- Must consume Slice 2 interfaces; it must not rewrite supervision or recovery
  authorization.
- Covers: ACC-012, ACC-013, ACC-014, partial ACC-016/022.
- Deliverable: one scope-bound atomic change produces authoritative before/
  after diff and immutable offline verification results or fails closed.

### Slice 4 — Deterministic L4 harness and evidence envelopes

- Executor: K3, with CX reviewing authority assertions.
- Depends on: Slices 1-3 stable product interfaces.
- May modify: dedicated `scripts/p9/**`, `tests/p9/**`, fixture manifests, and
  evidence schema/checkers; no product authority module.
- Covers: ACC-001, ACC-002 L4, ACC-003 L4, ACC-004, ACC-009/010 reproduction,
  and harness portions of ACC-015/016/022.
- Deliverable: one repeatable sandbox records network denial, uid/gid/modes,
  mounts/capabilities/socket denial, hostile fixtures, commands/exits, hashes,
  and one correlation without claiming L5/L6.

### Slice 5 — Packaging, performance, CI provenance, and claim audit

- Executor: K3.
- Depends on: stable product/harness outputs from Slices 1-4.
- May modify: build/provenance scripts, packaging tests, artifact manifests,
  required GitHub workflow steps, performance observation harness, and release
  evidence documents.
- Must not modify: authority, trust, or recovery decision code.
- Covers: ACC-017, ACC-019, ACC-020, ACC-021.
- Deliverable: exact-SHA wheel/CI artifact hashes, offline non-editable install,
  raw observed baseline, evidence inventory, and sentence-level claim mapping.

### Slice 6 — Correlated L4 chain and real isolated L6 recovery drill

- Executor: CX.
- Depends on: Slices 1-5 and a selected candidate SHA; candidate may advance
  only for defect fixes whose ownership returns to the responsible slice.
- May modify: dedicated isolated-runtime/recovery harness and focused
  defect-regression tests. Product fixes return to Slice 1, 2, or 3 ownership
  rather than being patched opportunistically here.
- Covers: ACC-001..017 and ACC-022 at L4; ACC-015/016 at L6.
- Deliverable: retained correlated L4 run plus a separate real isolated L6
  drill covering corruption, interruption, traversal, permission denial, and
  cleanup without production restore.

### Slice 7 — Real Windows main-Tauri L5 validation

- Executor: real Windows host; CX judges authority/trust evidence. K3 may
  prepare read-only evidence collection instructions.
- Depends on: Slice 6 candidate and Slice 5 identified package/artifacts.
- Repository modification scope: none by default. Any discovered defect returns
  to its owning slice and requires a new candidate SHA.
- Covers: ACC-002/003/005/006/010/012/018/019/022 at their L5 portions.
- Deliverable: standard-user Windows 10/Docker Desktop/WSL2/agent-dev identity,
  installed package hash, packaged main-Tauri PID/launch, authoritative view and
  action smoke, denied ACL/privilege cases, sanitized logs, and limitations.

## Candidate and concurrency rule

Slices 1-3 are authority work and execute in order under CX ownership. Slice 4
does not write authority code. Slice 5 does not start until product interfaces
are stable. Slice 6 consumes all prior artifacts. Slice 7 uses the exact
candidate produced after Slice 6; any host-discovered defect invalidates that
candidate and returns to the owning slice. No historical P8/P9 SHA may replace
the final candidate's evidence.
