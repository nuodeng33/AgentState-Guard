# R4-P9 Acceptance Contract — Alignment Revision

Contract state: `FROZEN`

Supersedes contract commit: `6f646045267cf9573b4fed0d77e2c08ca09bc134`

Superseded document: `docs/phases/09-acceptance-contract.md`

Revision base / P8 frozen SHA: `0b791d7e8abe3a503a22a99f53fc03bc624d8b07`

P8 state: `FROZEN`

P7 semantics: `FROZEN` and unchanged

P9 final integration SHA: `UNBOUND`

This revision does not erase or rewrite the old frozen contract. It supersedes
that contract because:

1. the old contract assigned local observations to L2, conflicting with the
   R4 definition of L2 as Unit;
2. P8 has since passed its final tribunal and is frozen; and
3. the P8 frozen SHA includes the tribunal CORS preflight fix.

The revision base is only the starting point for P9 work. It is not a future
P9 acceptance SHA. All P9 acceptance evidence must be rebound to the final P9
integration SHA selected after implementation.

## Scope and authority

R4 is the only active route. P0-P6 are complete, P7 semantics remain frozen,
and P8 is frozen at the revision base above. This contract defines acceptance
only. It adds no product feature, test implementation, frontend behavior,
workflow, restore capability, AI architecture, or deployment mechanism.

Normative terms:

- `must` and `must not` are acceptance requirements.
- `exact SHA` is the commit actually built, executed, and observed.
- `artifact` is immutable or content-hashed evidence with enough provenance
  to reproduce or audit the observation.
- `authoritative source` is server-owned or durable state from which a fact
  may be derived. Caller claims, UI state, AI output, filenames, and prose
  reports are not authoritative by themselves.
- an `observation` records a measured value and provenance. It is not an
  evidence level and has no PASS/FAIL meaning without a frozen threshold.

## Result vocabulary

| Result | Meaning |
| --- | --- |
| `PASS` | Every objective criterion is met at the required level and exact SHA. |
| `FAIL` | Execution produced an objective contradiction. |
| `NOT_RUN` | Required execution was not performed. |
| `NOT_VERIFIED` | A claim or artifact exists but provenance, level, or verification is insufficient. |
| `BLOCKED` | A named prerequisite prevents valid execution. |

No incomplete result may be described as expected to pass. `FROZEN` describes
this contract revision, not P9 acceptance.

## R4 evidence ladder

Evidence levels are cumulative only when lower-level evidence is bound to the
same exact SHA and a compatible environment. A higher level does not erase a
lower-level failure.

| Level | Name | Required meaning | Minimum provenance |
| --- | --- | --- | --- |
| L1 | Static | Source, schema, test definition, lint, `compileall`, workflow definition, or diff is inspected or validated without claiming execution behavior. | Exact SHA, worktree state, tool/version, command or method, timestamp, exit result. |
| L2 | Unit | A bounded unit executes with controlled inputs and asserts one component's behavior, including negative and fault cases. Mocks prove only the modeled boundary. | L1 fields plus test ID, fixture identity, runner version, result/count, and log or log hash. |
| L3 | Integration / CI | Multiple real components execute together, or a required CI workflow/job completes for the exact SHA. CI provenance must distinguish workflow definition from workflow success. | L2 fields plus component boundary or repository/run ID, workflow revision, jobs/conclusions, artifacts, and hashes where produced. |
| L4 | Isolated Runtime | The correlated product path runs offline, without a Docker socket, under restricted permissions in an isolated sandbox. | Lower-level fields plus sandbox definition, network-denial proof, identity/permission proof, mount/capability proof, immutable inputs, and complete step trace. |
| L5 | Real Target | The relevant path runs on the named real Windows target using the packaged main Tauri application where applicable. A GitHub-hosted Windows runner alone is L3, not L5. | Lower-level fields plus host/OS/runtime identity, package identity/hash, launch method, main-process identity, user/ACL context, and target logs. |
| L6 | Recovery Drill | A real isolated recovery drill exercises required success and failure cases, including corruption, interruption, traversal, and permission denial. | L4 fields plus immutable drill inputs, injected fault, recovery record, before/after hashes, Ledger verification, cleanup result, and retained drill artifact. |

Artifact hashes, local file observations, build durations, and performance
measurements are observation/provenance fields. No extra observation tier is
introduced.

Forbidden equivalences:

- test exists != test passed;
- mocked `PermissionError` != restricted-identity evidence;
- workflow exists != CI succeeded;
- CI Windows != L5 real Windows host;
- artifact exists != producing job succeeded;
- sidecar smoke != main Tauri smoke;
- historical SHA != final P9 SHA;
- checkpoint exists != checkpoint is recoverable;
- `R3` != `TRUSTED`.

## Evidence envelope

Every P9 evidence item must record:

- requirement and gate IDs;
- exact 40-character Git SHA and branch/detached state;
- relevant tracked and untracked worktree state;
- UTC start and end timestamps;
- environment, identity, permissions, and toolchain;
- complete method/command list and exit results;
- test/sample count where applicable;
- artifact names, sizes, and SHA-256 hashes;
- producing test/job conclusion;
- sanitized logs or log hashes; and
- limitations and deviations, even when empty.

A report copied from another SHA, an artifact without its producing result, or
a historical pass count without exact-SHA provenance is `NOT_VERIFIED`.

## Stable acceptance requirements

Requirement IDs and objective semantics remain those of the superseded
contract. ACC-017, ACC-019, and ACC-021 are evidence reclassifications only;
their objective acceptance criteria are unchanged.

| ID | Requirement | Required level | Objective PASS criteria |
| --- | --- | --- | --- |
| R4-P9-ACC-001 | Offline acceptance | L4 | The full isolated path succeeds with external network disabled; no required step attempts or depends on a Provider, registry, package download, remote API, or remote listener. Network denial and local success are both recorded. |
| R4-P9-ACC-002 | Normal-user and restricted-permission acceptance | L4 and L5 | The path completes as non-root/non-administrator. POSIX uid/gid/mode and Windows user/ACL denial cases are recorded; no step changes to a privileged identity. |
| R4-P9-ACC-003 | No Docker socket and no privilege escalation | L4 and L5 | No Docker socket/API or privileged mode is usable, no escalation path is used, and the accepted path still completes. Identity, mounts, capabilities, and denied access are recorded. |
| R4-P9-ACC-004 | Dirty and hostile environment | L4 | Bounded untracked, modified, stale, malformed, traversal, permission-denied, and conflicting-evidence fixtures preserve unrelated content, reject ambiguous authority, emit deterministic reason codes, and leak no sensitive content. |
| R4-P9-ACC-005 | Runtime discovery | L4 and L5 | Runtime facts are projected only from verified, allowlisted `RUNTIME_DETECTED` or `PROBE_UNREACHABLE` Ledger events. Empty, unreachable, conflicting, and stale inputs remain `EMPTY`, `UNKNOWN`, or `DEGRADED`, never unproven `AVAILABLE`/`RUNNING`. |
| R4-P9-ACC-006 | Agent discovery | L4 and L5 | Agent facts are projected only from verified, allowlisted `AGENT_DETECTED` Ledger events. Static confidence remains bounded, ambiguous process/deployment binding fails closed, and caller values never become authority. |
| R4-P9-ACC-007 | Workspace binding | L4 | Each accepted target is bound to one server-owned execution domain and workspace identity. Missing, stale, cross-workspace, or conflicting bindings prevent mutation. Raw or caller-supplied paths never become authority. |
| R4-P9-ACC-008 | Write-before deterministic policy | L4 | Before mutation, local policy durably records one outcome using `BLOCK > REVIEW > UNKNOWN > ALLOW`. BLOCK/UNKNOWN do not execute; REVIEW waits; approval cannot clear checkpoint requirements. |
| R4-P9-ACC-009 | AI offline fallback | L4 | Both no-AI-required and AI-required-but-unavailable paths run. AI remains optional, sanitized, and non-authoritative; absence/error/output cannot create facts, weaken policy, approve, authorize, grant trust, or change deterministic state. |
| R4-P9-ACC-010 | Explicit user approval | L4 | One current same-session action binding is consumed exactly once. Replay, stale, malformed, cross-session, terminal, recovery-bound, and concurrent actions fail closed. Approval records one `USER_APPROVED` event and neither activates nor creates a checkpoint nor changes policy to ALLOW. |
| R4-P9-ACC-011 | Checkpoint binding | L4 | A required checkpoint is created before change and bound to session, workspace, scope, exact SHA, and immutable manifest. Missing, stale, corrupt, or mismatched evidence prevents activation/change. |
| R4-P9-ACC-012 | Controlled change | L4 | Exactly the approved scope changes through the authoritative path bound to policy, approval, checkpoint, workspace, and exact SHA. Scope drift, replay, reorder, or partial effects fail atomically. |
| R4-P9-ACC-013 | Post-change diff | L4 | An authoritative before/after diff is bound to the change and checkpoint, accounts for all in-scope effects, and proves unrelated hostile/dirty content was preserved. Missing/inconsistent diff prevents completion. |
| R4-P9-ACC-014 | Offline verification | L4 | Verification runs after diff with network disabled and local authoritative inputs, records methods/exits, and blocks completion on failed, missing, stale, or scope-mismatched verification. |
| R4-P9-ACC-015 | Isolated test-restore and recovery | L4 and L6 | Test-restore writes only to an isolated destination and proves content/metadata without production-state writes. L6 additionally covers corruption, interruption, traversal, and permission denial. |
| R4-P9-ACC-016 | Evidence Ledger verification | L4 and L6 | Ledger verification occurs before authority projection and after the final event. The correlated range is complete, ordered, hash-linked, and bound to session/artifacts; missing, duplicate, reordered, corrupt, or unbound evidence invalidates dependent claims. |
| R4-P9-ACC-017 | Wheel and package provenance | L3; L4 for offline installation | The exact-SHA wheel is linked to a successful build, named/sized/SHA-256 hashed, installed non-editably into a clean offline environment, passes dependency and entry-point smoke, and matches the CI artifact or records independent provenance. Hashes and local artifact observations are provenance, not a level. |
| R4-P9-ACC-018 | Windows and Tauri validation | L5 | A real Windows 10/Docker Desktop/WSL2/`agent-dev` record identifies the installed package and launches the packaged main Tauri executable. Sidecar-only readiness and MSI existence do not pass; MSI claims require install, main-process launch, and accepted view/action smoke. |
| R4-P9-ACC-019 | Performance observations | Observation provenance; L5 for target claims | `OBSERVED_BASELINE` records exact SHA, environment, method, samples, values, units, aggregation, raw artifact hash, and limitations. No latency PASS/FAIL is allowed without a separately frozen threshold. |
| R4-P9-ACC-020 | Exact-SHA CI and evidence provenance | L3 | Every required workflow/job conclusion, log, and artifact is bound to the final integration SHA. Historical/different-SHA observations are segregated. |
| R4-P9-ACC-021 | Release-claim boundaries | Claim audit plus the level required by each claim | Every release sentence maps to requirements/evidence. Over-level claims are removed/narrowed; AI is absent from fact/authorization chains; R3 differs from TRUSTED; checkpoint differs from recoverability. The audit itself is provenance, not L2. |
| R4-P9-ACC-022 | Full correlated MVP E2E | L4 and L5 | One correlation binds the complete frozen chain with no caller authority, skipped step, simulated success, cross-run splice, or unrecorded prerequisite. P8 remains frozen and its action semantics are reproduced at the final P9 SHA. |

IDs `ACC-001..022` are permanent. A future
revision must explicitly add or supersede an ID and must not silently reuse it.

## Unique MVP chain

One correlation must bind all twelve steps. Unit, mocked, sidecar, historical,
and separate-run results cannot be spliced into a full MVP claim.

| Step | Authoritative source | Required level | Fail-closed minimum |
| --- | --- | --- | --- |
| 1 Runtime discovery | Verified allowlisted runtime/probe Ledger events | L4 and L5 | Empty, unreachable, ambiguous, stale, or invalid-Ledger facts stop the chain. |
| 2 Agent discovery | Verified allowlisted agent Ledger events | L4 and L5 | Static/ambiguous evidence cannot become a live uniquely bound agent. |
| 3 Workspace binding | Server-owned domain/workspace binding | L4 | Missing/conflicting binding prevents mutation and exposes no raw path. |
| 4 Deterministic policy | Local policy plus durable `POLICY_EVALUATED` | L4 | BLOCK/UNKNOWN stop; REVIEW waits; no AI/caller weakening. |
| 5 AI only if required | Non-authoritative AI attempt/outcome | L4 | Absence/error/output cannot create facts, approval, trust, or ALLOW. |
| 6 Explicit approval | Durable session, current action binding, `USER_APPROVED` | L4 and L5 | Wrong, stale, replayed, terminal, or recovery-bound action stays unchanged. |
| 7 Checkpoint | Immutable manifest and server-owned session/checkpoint binding | L4 | Missing/invalid/mismatched checkpoint prevents activation/change. |
| 8 Controlled change | Authority-bound transaction/change record | L4 and L5 | Scope drift, replay, partial write, or authority mismatch rolls back/stops. |
| 9 Post-change diff | Bound authoritative before/after state | L4 | Missing/incomplete/mismatched diff prevents completion. |
| 10 Offline verification | Local verifier and immutable run record | L4 | Failed, skipped, stale, or network-dependent verification stops completion. |
| 11 Isolated test-restore | Snapshot V3 and durable recovery result | L4; L6 for recoverability | Production target or any fault/mismatch fails without trust promotion. |
| 12 Ledger verification | `verify_ledger()` over the correlated range | L4 and L6 | Missing, duplicate, reordered, corrupt, or unbound events invalidate claims. |

Approval never creates broader recovery authority. P7 recovery continues to
use its one-time, nonce-bound, expiry-bound authorization path. `R3` remains
separate from the Trusted Baseline authority.

## Performance contract

P9 freezes no latency, throughput, memory, CPU, startup, or package-size SLA.
A performance record is accepted only as an `OBSERVED_BASELINE` with the
provenance required by ACC-019. Configured timeouts, retries, readiness gates,
workflow durations, and prose tables without raw provenance are not results.

## P8 prerequisites

| Prerequisite | Revision-time state | Final P9 rule |
| --- | --- | --- |
| K3 action wiring | `PASS` at P8 frozen SHA | Must remain present and unchanged or explicitly revalidated. |
| P8 integrated action E2E | `PASS` at P8 frozen SHA | Must be reproduced at the final P9 integration SHA for P9 evidence. |
| P8 final tribunal | `PASS`; P8 `FROZEN` | Frozen semantics must not be weakened. |
| Tribunal CORS fix | Included in `0b791d7e8abe3a503a22a99f53fc03bc624d8b07` | Allowed preflight must remain functional and real requests token-protected. |
| P9 final integration SHA | `BLOCKED` / `UNBOUND` | Select only after P9 implementation; bind every acceptance run to it. |

## Mandatory P9 gates

All gates must be `PASS` at one final integration SHA. `NOT_RUN`,
`NOT_VERIFIED`, `BLOCKED`, or `FAIL` prevents P9 acceptance.

| Gate | Mechanical PASS condition |
| --- | --- |
| P9-GATE-001 P8 frozen | Tribunal names `0b791d7e8abe3a503a22a99f53fc03bc624d8b07`, records P8 FROZEN, and the final P9 candidate preserves its semantics. |
| P9-GATE-002 Exact integration SHA | One future 40-character P9 SHA is selected; every run/artifact is bound to it and relevant worktree state is recorded. |
| P9-GATE-003 Python regression | All L2 unit tests and all L3 integration/CI tests are collected and executed at the exact SHA with counts, skips, failures, duration, exit, and log hash; zero unexplained failures. |
| P9-GATE-004 Frontend regression | Frozen frontend tests, typecheck, and production build execute at the exact SHA with toolchain, counts, exits, and artifact/log hashes; zero unexplained failures. |
| P9-GATE-005 Static/diff checks | L1 `compileall`, frozen Ruff scope, contract validators, and `git diff --check` record commands, versions, scopes, and zero exits. |
| P9-GATE-006 Exact-SHA CI | Every required workflow/job succeeds for the final SHA with L3 run/job provenance and artifact links/hashes. |
| P9-GATE-007 Offline acceptance | ACC-001, ACC-009, ACC-014, and the L4 part of ACC-022 pass. |
| P9-GATE-008 Permission acceptance | ACC-002 and ACC-003 pass at both L4 and L5. |
| P9-GATE-009 Dirty-environment acceptance | ACC-004 passes with unrelated-content hashes and all named hostile cases. |
| P9-GATE-010 Wheel provenance | ACC-017 passes through build, artifact hash, clean offline non-editable install, dependency check, entry-point smoke, and CI linkage. |
| P9-GATE-011 P8 action E2E | At final P9 SHA, packaged UI/backend cover approve, reject, stale, replay, concurrency, recovery isolation, failure, and CORS semantics. |
| P9-GATE-012 Full MVP and target E2E | ACC-005 through ACC-016, ACC-018, and ACC-022 pass at required L4/L5 in one correlation. |
| P9-GATE-013 Recovery evidence | L6 parts of ACC-015/016 pass for corruption, interruption, traversal, and permission denial, bounded to isolated recovery. |
| P9-GATE-014 Performance baseline | ACC-019 has a complete observation envelope/raw hash and no invented threshold verdict. |
| P9-GATE-015 Evidence inventory | Every requirement/gate has one result; every artifact hash resolves; historical/different-SHA evidence is segregated. |
| P9-GATE-016 Release claim audit | ACC-021 passes and every release claim stays within its proven level. |
| P9-GATE-017 Remaining limitations | A sanitized artifact lists residual unsupported platforms/scopes/failures without mislabeling any gap as accepted. |

The gate evaluator must emit gate ID, result, evidence references, and a stable
objective reason code. It must never infer PASS from missing data.

## Out of scope

P9 does not add or accept production restore, Windows/WSL recovery, Android or
Device Link features, TLS/mDNS, remote listeners, Docker socket access, root
operation, background watchers, a new AI architecture, or general container
management. L6 remains a real isolated drill and does not expand production
restore authority.

## Revision change control

This revision preserves ACC-001..022 objective semantics. The only semantic
alignment is the canonical R4 evidence ladder and the resulting evidence
classification for ACC-017, ACC-019, ACC-021, and regression gates. P8
prerequisite states are updated from blocked to frozen/pass, while future P9
exact-SHA evidence remains unbound. No historical observation is promoted.

Future contract changes require a dedicated documentation commit that names
added/superseded IDs, explains the authority conflict, preserves anti-inflation
and release boundaries, and contains no product, test, frontend, or CI change.
