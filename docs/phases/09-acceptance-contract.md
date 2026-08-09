# R4-P9 Acceptance Contract

Contract state: `FROZEN`

Contract base SHA: `3483e5b8f5b1f69ecaa7441c4e8b7ecdd70c17de`

This document freezes the acceptance semantics for R4-P9. It does not record
P9 acceptance, freeze P8, or declare the R4 MVP complete. Evidence produced
before the final integration SHA may be inventoried, but it cannot satisfy an
exact-SHA gate unless it is reproduced or independently proven to apply to that
exact SHA.

## Scope and authority

R4 is the only active route. P0-P6 are complete and P7 semantics are frozen.
P8 read backend, K3 frontend, and action backend are inputs to this contract,
but P8 remains unfrozen until K3 action wiring and the P8 final tribunal are
complete.

This contract defines acceptance only. It adds no product feature, test
implementation, frontend behavior, workflow, restore capability, AI
architecture, or deployment mechanism.

Normative terms:

- `must` and `must not` are acceptance requirements.
- `exact SHA` means the commit that was actually built, executed, and observed.
- `artifact` means immutable or content-hashed evidence with enough provenance
  to reproduce or audit the observation.
- `authoritative source` is the server-owned or durable source from which a fact
  may be derived. A caller claim, UI state, AI output, filename, or prose report
  is not authoritative by itself.

## Result vocabulary

Each requirement and gate has exactly one result:

| Result | Meaning |
| --- | --- |
| `PASS` | Every objective criterion is met by the required evidence level at the exact SHA. |
| `FAIL` | Execution produced an objective result that contradicts a criterion. |
| `NOT_RUN` | The required execution was not performed. |
| `NOT_VERIFIED` | A claim or artifact exists, but required provenance, level, or verification is missing. |
| `BLOCKED` | A named prerequisite is not complete, so execution cannot yet produce valid evidence. |

No incomplete item may be described as expected to pass. `FROZEN` describes
this contract only and is not a requirement or gate result.

## Evidence model

Evidence levels are cumulative only when every lower-level artifact remains
bound to the same exact SHA and compatible environment. Higher-level evidence
does not erase a lower-level failure.

| Level | Name | Required meaning | Minimum provenance |
| --- | --- | --- | --- |
| L1 | Static | Source, schema, lint, `compileall`, test definition, or workflow definition was inspected or validated. | Exact SHA, clean/dirty state, tool version, command or method, timestamp, exit result. |
| L2 | Local Observable | A local execution result, log, artifact, or hash was observed at the exact SHA. | L1 fields plus environment identity, artifact path/name, SHA-256, and observed result. |
| L3 | Integration / CI | A GitHub run and required jobs completed for the exact SHA, with traceable logs and artifacts. | Repository, exact SHA, run URL/ID, workflow revision, job names and conclusions, artifact names and hashes. |
| L4 | Isolated Runtime | The end-to-end path ran offline, without a Docker socket, and with restricted permissions in an isolated sandbox. | L2 fields plus sandbox definition, network proof, identity/permission proof, mount/capability proof, and complete step trace. |
| L5 | Real Target Platform | The relevant path ran on real Windows 10, Docker Desktop, WSL2, and `agent-dev`, using the packaged Tauri application where applicable. | L2 fields plus host/OS/runtime versions, package identity, launch method, process identity, and target-specific logs. |
| L6 | Recovery Drill | A real isolated recovery drill exercised required success and failure cases, including corruption, interruption, traversal, and permission denial. | L4 fields plus immutable inputs, injected fault, recovery record, before/after hashes, Ledger verification, and retained drill result. |

The following equivalences are forbidden:

- test code exists != test passed;
- workflow exists != CI succeeded;
- artifact exists != the producing tests succeeded;
- sidecar smoke != main Tauri application smoke;
- checkpoint exists != checkpoint is recoverable;
- `R3` != `TRUSTED`;
- a lower evidence level != a higher evidence level.

## Evidence envelope

Every P9 evidence item must record:

- requirement and gate IDs;
- exact 40-character Git SHA and branch or detached state;
- worktree status, including untracked files relevant to the run;
- UTC start and end timestamps;
- environment and toolchain identities;
- complete method or command list with exit results;
- sample count when observations are repeated;
- artifact names, byte sizes, and SHA-256 hashes;
- producing test/job conclusion, where applicable;
- sanitized logs or log hashes;
- limitation and deviation fields, even when empty.

A report copied from another SHA, an artifact without its producing result, or a
historical pass count without exact-SHA provenance is `NOT_VERIFIED`.

## Stable acceptance requirements

| ID | Requirement | Required evidence | Objective pass criteria |
| --- | --- | --- | --- |
| R4-P9-ACC-001 | Offline acceptance | L4 | The full isolated path completes with external network disabled; no required step attempts or depends on a Provider, registry, package download, remote API, or remote listener. Network denial and the successful local result are both recorded. |
| R4-P9-ACC-002 | Normal-user and restricted-permission acceptance | L4 and L5 | The required path completes as a non-root, non-administrator identity. POSIX uid/gid/mode and Windows user/ACL denial cases are exercised and recorded; no step relies on changing to a privileged identity. |
| R4-P9-ACC-003 | No Docker socket and no privilege escalation | L4 and L5 | The runtime has no Docker socket mount or usable Docker API, is not privileged, has no privilege-escalation path used by the test, and still completes the accepted path. Identity, mounts, capabilities, and denied access are recorded. |
| R4-P9-ACC-004 | Dirty and hostile environment | L4 | Bounded untracked, modified, stale, malformed, traversal, permission-denied, and conflicting-evidence fixtures are exercised. The system preserves unrelated content, rejects ambiguous authority, and records deterministic reason codes without leaking sensitive content. |
| R4-P9-ACC-005 | Runtime discovery | L4 and L5 | Runtime facts are projected only from allowlisted, verified `RUNTIME_DETECTED` or `PROBE_UNREACHABLE` Evidence Ledger events. Empty, unreachable, conflicting, and stale inputs fail to `EMPTY`, `UNKNOWN`, or `DEGRADED`; they never become unproven `AVAILABLE` or `RUNNING`. |
| R4-P9-ACC-006 | Agent discovery | L4 and L5 | Agent facts are projected only from allowlisted, verified `AGENT_DETECTED` Ledger events. Static evidence remains bounded by its declared confidence; ambiguous process or deployment binding fails closed and no caller value becomes agent authority. |
| R4-P9-ACC-007 | Workspace binding | L4 | Each accepted target is bound to one server-owned execution domain and workspace identity. Missing, stale, cross-workspace, or conflicting bindings remain explicit and prevent mutation. No absolute path or caller-supplied binding becomes authoritative. |
| R4-P9-ACC-008 | Write-before local policy | L4 | Before any mutation, the deterministic local policy records one authoritative outcome with `BLOCK > REVIEW > UNKNOWN > ALLOW` precedence. `BLOCK` and `UNKNOWN` do not execute; `REVIEW` waits for explicit approval; a checkpoint requirement cannot be cleared by approval. |
| R4-P9-ACC-009 | AI offline fallback | L4 | Both the no-AI-required path and the AI-required-but-unavailable offline path are exercised. AI is optional, sanitized, and non-authoritative; it cannot create runtime facts, weaken policy, approve, authorize, or grant trust. Missing or failed AI leaves the deterministic policy and authorization state unchanged and fails closed. |
| R4-P9-ACC-010 | Explicit user approval | L4 | A server-owned `REVIEW` session in `AWAITING_APPROVAL` accepts exactly one valid, current, same-session action binding. Replay, stale, malformed, cross-session, terminal, recovery-specific, and concurrent actions fail closed. Approval records one `USER_APPROVED` event and does not activate, create a checkpoint, or change policy to `ALLOW`. |
| R4-P9-ACC-011 | Checkpoint | L4 | A checkpoint is created before the controlled change when policy requires it, is bound to the approved session, workspace, scope, and immutable manifest, and is hash-verifiable. Missing, stale, corrupt, or mismatched checkpoint evidence prevents activation or change. |
| R4-P9-ACC-012 | Controlled change | L4 | Exactly the approved scope is changed through the authoritative transaction/change path and is bound to policy, approval, checkpoint, workspace, and exact SHA. Out-of-scope, reordered, replayed, or partially committed effects fail atomically and are reported without authority escalation. |
| R4-P9-ACC-013 | Post-change diff | L4 | A post-change diff is generated from authoritative before/after state, is bound to the controlled change and checkpoint, identifies every in-scope effect, and proves unrelated hostile/dirty content was preserved. Missing or inconsistent diff evidence prevents completion. |
| R4-P9-ACC-014 | Offline verification | L4 | Verification runs after the diff with network disabled, uses only local authoritative inputs, records commands/methods and exit results, and blocks completion on failed, missing, stale, or scope-mismatched verification. |
| R4-P9-ACC-015 | Isolated test-restore and recovery | L4 and L6 | Test-restore runs only in an isolated destination and proves before/after content and metadata without writing to production state. An L6 drill additionally covers corruption, interruption, traversal, and permission denial. A checkpoint alone never satisfies this requirement. |
| R4-P9-ACC-016 | Evidence Ledger verification | L4 and L6 | The append-only R4 Evidence Ledger verifies before authority is projected and after the final event. The accepted event range is complete, ordered, hash-linked, and bound to the session and artifacts. Missing, duplicate, reordered, corrupt, or unverifiable evidence invalidates the affected authority and completion claim. |
| R4-P9-ACC-017 | Wheel and package provenance | L2 and L3 | The wheel is built from the exact SHA, named and SHA-256 hashed, linked to its producing successful build, installed non-editably into a clean offline environment, passes dependency and entry-point smoke, and matches the CI artifact or records the independently built provenance. |
| R4-P9-ACC-018 | Windows and Tauri validation | L5 | A real Windows 10 / Docker Desktop / WSL2 / `agent-dev` record includes installed package identity and launches the packaged main Tauri executable. Sidecar-only readiness and MSI-file existence do not pass; an MSI claim requires installation followed by main-process launch and accepted view/action smoke. |
| R4-P9-ACC-019 | Performance observations | L2, with L5 for target-platform observations | An `OBSERVED_BASELINE` records exact SHA, environment, method, sample count, and observed values. Results are reported without a PASS/FAIL latency judgment unless a separately frozen threshold exists. Timeout or gate configuration alone is `NOT_RUN`. |
| R4-P9-ACC-020 | Exact-SHA CI and evidence provenance | L3 | Every required workflow and job conclusion, log, and artifact is bound to the final integration SHA. The evidence inventory distinguishes current exact-SHA results from historical or different-SHA observations. Missing linkage is `NOT_VERIFIED`. |
| R4-P9-ACC-021 | Release-claim boundaries | L2 audit plus the level required by each claim | A claim audit maps every release sentence to requirement IDs and evidence. Claims exceeding the available level are removed or narrowed; AI is absent from fact and authorization chains; `R3` is distinct from `TRUSTED`; checkpoint existence is distinct from recoverability. |
| R4-P9-ACC-022 | Full MVP end-to-end chain | L4 and L5 | One correlated run completes every step in the frozen chain below with no caller-supplied authority, skipped step, simulated success, cross-run evidence splice, or unrecorded prerequisite. K3 action wiring and P8 freeze must be complete before this requirement can run. |

Requirement IDs are permanent. A future change may add a new ID or explicitly
supersede an ID, but must not silently redefine or reuse an existing ID.

## Unique MVP end-to-end chain

One correlation ID must bind all twelve steps. Separate unit, mocked, sidecar,
or historical runs cannot be spliced together and called the full MVP E2E.

| Step | Authoritative source | Required level | Minimum artifact | Fail-closed behavior |
| --- | --- | --- | --- | --- |
| 1. Runtime discovery | Verified, allowlisted `RUNTIME_DETECTED` / `PROBE_UNREACHABLE` Ledger evidence | L4 and L5 | Sanitized discovery input digest, resulting event refs, projection, and runtime/process evidence | Empty, unreachable, ambiguous, stale, or invalid-Ledger facts remain non-authoritative and stop the chain. |
| 2. Agent discovery | Verified, allowlisted `AGENT_DETECTED` Ledger evidence | L4 and L5 | Input digest, agent event refs, projected identity/status, and conflict cases | Static or ambiguous evidence cannot be promoted to a live or uniquely bound agent. |
| 3. Workspace binding | Server-owned execution-domain and workspace binding derived from authoritative discovery | L4 | Binding ID/digest, scope digest, evidence refs, and negative cross-workspace case | Unknown or conflicting binding prevents policy evaluation for mutation and exposes no raw path. |
| 4. Write-before local policy | Deterministic local policy and durable `POLICY_EVALUATED` Evidence | L4 | Structured fact digest, matched rule IDs, decision, reason code, checkpoint/approval flags | `BLOCK` and `UNKNOWN` stop; `REVIEW` waits; no AI or caller value weakens the decision. |
| 5. AI only if required | Non-authoritative AI attempt/outcome record plus unchanged policy/session authority | L4 | Sanitized traces for bypass and unavailable fallback, with policy and session before/after | AI absence, error, or output cannot create facts, approval, authorization, trust, or `ALLOW`. |
| 6. Explicit user approval | Durable Supervision session, current opaque action binding, and `USER_APPROVED` Ledger event | L4 and L5 | K3-to-backend action trace, request/response reason codes, session transition, event ref, replay/concurrency negatives | Stale, replayed, malformed, wrong-session, terminal, or recovery-bound actions remain unchanged. |
| 7. Checkpoint | Snapshot V3 manifest and server-owned session/checkpoint binding | L4 | Manifest, content hashes, scope/binding IDs, creation result, corruption/mismatch negatives | Missing or invalid checkpoint prevents activation and controlled change. |
| 8. Controlled change | Authoritative transaction/change record bound to policy, approval, checkpoint, and scope | L4 and L5 | Change ID, approved scope digest, atomic result, Evidence refs, before/after state hashes | Scope drift, replay, partial write, or authority mismatch rolls back or stops without completion. |
| 9. Post-change diff | Authoritative before/after state and checkpoint/change binding | L4 | Sanitized diff, diff hash, scope reconciliation, unrelated-content preservation result | Missing, incomplete, or mismatched diff prevents completion. |
| 10. Offline verification | Local verification implementation and immutable run record | L4 | Offline proof, commands/method, exit results, logs/hashes, exact-SHA link | Any failed, skipped, stale, or network-dependent verification prevents completion. |
| 11. Isolated test-restore | Snapshot V3, durable recovery records, and R2 test-restore result | L4, plus L6 for recoverability claims | Isolated destination identity, restore manifest, before/after hashes, no-production-write proof, fault cases | Production target, traversal, corruption, interruption, permission failure, or mismatch fails without trust promotion. |
| 12. Evidence Ledger verification | `verify_ledger()` over the complete correlated event range | L4 and L6 | First/last event refs, event count, final chain hash, verification result, complete evidence inventory hash | Missing, reordered, duplicate, corrupt, or unbound events invalidate the chain and all dependent claims. |

Steps 6 through 8 are `BLOCKED` until K3 action wiring is integrated and
accepted. Step 6 approval does not itself authorize activation, create a
checkpoint, or execute a change. Any broader recovery authorization continues
to use the P7 one-time, nonce-bound, expiry-bound recovery path.

## Performance contract

P9 freezes no latency, throughput, memory, CPU, startup, or package-size SLA.
The only accepted performance result is an `OBSERVED_BASELINE` containing:

- exact SHA;
- environment and relevant versions;
- method and workload;
- warm-up policy, if any;
- sample count;
- individual or losslessly summarized observed values;
- units and aggregation method;
- raw artifact name and SHA-256;
- known measurement limitations.

Absent a separately frozen threshold, the baseline has no performance
`PASS`/`FAIL` conclusion. A configured timeout, retry count, readiness gate, or
workflow duration is not an observed baseline.

## Release claim gate

Release claims are capped by the highest relevant evidence level actually
present for the exact SHA:

- Without L3, do not claim exact integration or CI is proven.
- Without L4, do not claim a complete offline or restricted-runtime loop.
- Without L5, do not claim real target-platform or main Tauri application
  validation.
- Without L6, do not make an unconditional recoverability claim.
- AI output must not enter the fact chain or authorization chain.
- `R3` must not be described as `TRUSTED` without the separate P7 trusted
  baseline authority.
- A checkpoint must not be described as recoverable without accepted restore
  evidence.

The release-claim audit itself passes only when every public claim has an
evidence mapping or has been removed/narrowed to the available level.

## P8 prerequisites

| Prerequisite | Current contract-time result | Unblock condition |
| --- | --- | --- |
| K3 action frontend wiring integrated | `BLOCKED` | K3 wiring is merged into the candidate integration SHA and its frontend/backend action evidence is inventoried. |
| P8 integrated action E2E | `BLOCKED` | One accepted K3-to-authoritative-backend approval/rejection run covers success, stale, replay, recovery isolation, and failure semantics. |
| P8 final tribunal | `BLOCKED` | The tribunal verifies read, frontend, action backend, and integrated wiring at one exact SHA and records P8 `FROZEN`. |
| P9 final integration SHA | `BLOCKED` | P8 is frozen and the P9 acceptance candidate SHA is selected without uncommitted relevant changes. |

P8 completion claims outside this table are not created by this contract.

## Known preflight gaps encoded

| Gap | Requirement/gate mapping | Contract-time result |
| --- | --- | --- |
| Historical pass counts lack current exact-SHA provenance | R4-P9-ACC-020; P9-GATE-003 through P9-GATE-006 | `NOT_VERIFIED` |
| Local wheel exists but complete production and clean-install provenance is absent | R4-P9-ACC-017; P9-GATE-010 | `NOT_VERIFIED` |
| P5 AI Supervisor documentation and implementation status drift | R4-P9-ACC-009 and R4-P9-ACC-021 | `NOT_VERIFIED` |
| Windows smoke primarily starts the sidecar, not the main Tauri executable | R4-P9-ACC-018; P9-GATE-012 | `NOT_VERIFIED` |
| MSI smoke does not launch the installed main application | R4-P9-ACC-018; P9-GATE-012 | `NOT_VERIFIED` |
| Offline and permission evidence is substantially mocked or controlled | R4-P9-ACC-001 through R4-P9-ACC-004; P9-GATE-007 through P9-GATE-009 | `NOT_VERIFIED` |
| Real restricted-user, ACL, chmod, and uid E2E evidence is absent | R4-P9-ACC-002 and R4-P9-ACC-003; P9-GATE-008 | `NOT_RUN` |
| Existing performance material is configuration rather than observation | R4-P9-ACC-019; P9-GATE-014 | `NOT_RUN` |
| P8 integrated action E2E awaits K3 wiring | R4-P9-ACC-010 and R4-P9-ACC-022; P9-GATE-001 and P9-GATE-011 | `BLOCKED` |

This table records the preflight classification, not a permanent result.
Changing a result requires a new exact-SHA evidence item; editing prose is not
sufficient.

## Mandatory P9 gates and exit criteria

P9 acceptance requires all gates below to be `PASS` at one final integration
SHA. `NOT_RUN`, `NOT_VERIFIED`, `BLOCKED`, or `FAIL` prevents P9 acceptance.

| Gate | Mechanical PASS condition |
| --- | --- |
| P9-GATE-001 P8 frozen | An authoritative P8 tribunal record names the exact integration SHA and records P8 `FROZEN`; all P8 prerequisite rows above are unblocked. |
| P9-GATE-002 Exact integration SHA | The evidence inventory names one 40-character SHA; all acceptance executions and artifacts are bound to it, and relevant worktree state is recorded. |
| P9-GATE-003 Python full regression | The complete Python test suite is collected and executed at the exact SHA; counts, skips, failures, exit result, duration, and log hash are recorded. PASS requires zero unexplained failures. |
| P9-GATE-004 Frontend regression | The complete frozen frontend regression set is executed at the exact SHA; toolchain, counts, exit result, and log/artifact hashes are recorded. PASS requires zero unexplained failures. |
| P9-GATE-005 Static and diff checks | `compileall`, the frozen Ruff scope, and `git diff --check` run at the exact SHA with commands, versions, scope, and zero exits recorded. |
| P9-GATE-006 Exact-SHA CI | Every required GitHub workflow/job concludes successfully for the exact SHA, with L3 provenance and producing artifact links/hashes. |
| P9-GATE-007 Offline acceptance | R4-P9-ACC-001, R4-P9-ACC-009, R4-P9-ACC-014, and the L4 portion of R4-P9-ACC-022 are `PASS`. |
| P9-GATE-008 Permission acceptance | R4-P9-ACC-002 and R4-P9-ACC-003 are `PASS` at their required L4 and L5 levels. |
| P9-GATE-009 Dirty-environment acceptance | R4-P9-ACC-004 is `PASS` with preserved unrelated-content hashes and all named hostile cases recorded. |
| P9-GATE-010 Wheel provenance and clean install | R4-P9-ACC-017 is `PASS`; build, CI production, artifact hash, clean offline non-editable install, dependency check, and entry-point smoke form one auditable chain. |
| P9-GATE-011 P8 action integrated E2E | K3 action wiring and backend execute one exact-SHA integrated run with approve, reject, stale, replay, concurrency, recovery-isolation, and failure outcomes matching the P8 action contract. |
| P9-GATE-012 Full MVP and target-platform E2E | R4-P9-ACC-005 through R4-P9-ACC-016, R4-P9-ACC-018, and R4-P9-ACC-022 are `PASS` at the required L4/L5 levels in one correlated run. |
| P9-GATE-013 Recovery evidence | The L6 portions of R4-P9-ACC-015 and R4-P9-ACC-016 are `PASS`, including corruption, interruption, traversal, and permission cases; claims remain bounded to isolated test-restore. |
| P9-GATE-014 Performance observed baseline | R4-P9-ACC-019 has a complete `OBSERVED_BASELINE` and raw artifact hash; no invented SLA or threshold verdict appears. |
| P9-GATE-015 Evidence inventory | Every gate and requirement has one result and links to its exact-SHA evidence envelope; every artifact hash resolves; historical/different-SHA evidence is segregated. |
| P9-GATE-016 Release claim audit | R4-P9-ACC-021 is `PASS`; every release statement is within L1-L6 claim bounds and no AI, R3, checkpoint, sidecar, workflow, or artifact equivalence is overstated. |
| P9-GATE-017 Remaining limitations | A sanitized limitations artifact lists all residual unsupported platforms, scopes, failure modes, and evidence gaps, with no unresolved item mislabeled as accepted. |

The gate evaluator must emit the gate ID, result, evidence references, and an
objective reason code. It must not infer `PASS` from missing data.

## Out of scope

P9 does not add or accept:

- production restore;
- Windows or WSL recovery;
- Android or Device Link features;
- TLS or mDNS;
- a remote listener;
- Docker socket access;
- root or privileged operation;
- a background watcher;
- a new AI architecture;
- general container management.

L6 evidence in this contract is limited to real isolated recovery drills. It
does not expand the product into production restore or Windows/WSL recovery.

## Contract change control

Changes to this frozen contract require a dedicated documentation commit that:

1. identifies every added or superseded requirement ID;
2. explains why existing evidence cannot be evaluated under the frozen text;
3. preserves the L1-L6 anti-inflation rules and release-claim boundaries;
4. does not retroactively convert existing evidence to a higher level; and
5. does not alter product code, tests, frontend, or CI as part of the contract
   change.

P9 execution batches may attach evidence and results to this contract, but they
must not silently weaken its criteria.
