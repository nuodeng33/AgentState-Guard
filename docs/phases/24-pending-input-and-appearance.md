# Phase 24 — pending instructions and saved agent colors

## 2026-10-24 phase 24 startup and shutdown follow-up

The approved pending instruction and agent color features are implemented in `3dec5d9`. Its Core CI failed before browser launch. Real disposable Core startup reproduced two independent initialization races: stale migration replay and concurrent first WAL initialization. A full regression also exposed shutdown rereading a run ID cleared by the real file finalizer. The final minimal fix serializes Core bootstrap before its workers, rechecks each migration under the write lock without replacing history, and snapshots shutdown run IDs. Expected database failures keep the existing safe HTTP 503/degraded contracts; unexpected errors still propagate. No permissions, schema version, public API or historical ledger values change in this follow-up.

Independent final backend: **1542 passed, 49 skipped, 0 failures/errors** (JUnit 1591 total). Real fresh Core startup **12/12 passed**, all threads stopped after cleanup; real HTTP queue/restart acceptance passed with controlled no-model provider and real file capture. Deterministic shutdown RED became GREEN without cancelling the completed run. Actual Windows SQLite concurrent migration from versions 0, 2 and 19 passed with old migration rows preserved. Frontend remains the separately verified 317-test snapshot; it was unchanged here.

`3dec5d9` Windows and both Android workflows succeeded; its Core failure and intermediate local failures remain recorded. The next exact commit CI, strict browser queue acceptance and new Windows package verification are still required. Existing 4a8 native upgrade/uninstall evidence remains valid for its own scope. The user's six original data files are restored; do not start or migrate them. Computer Use remains stopped by physical Escape; no Windows/Android UI inputs without fresh authorization, and MuMu 2/5 remain protected. No merge, publication, reboot or permanent cleanup. See [source verification](../verification/PHASE24_STARTUP_RECOVERY_20261024.json) and [prior native phase24 checks](../verification/PHASE24_NATIVE_3DEC_20261024.json).

Requirement IDs: ASG-CLOSE-002 (saved agent color), ASG-CLOSE-006 (explicit next instruction). Base product: `4a8e32046e74ca75824462376fe9d20d5f77673b`.

The user explicitly approved: “允许最小兼容扩展，继续实现两项功能”, in response to the question proposing two new tables and independent APIs while preserving compatibility, the historical ledger and permissions. The earlier API/schema freeze is lifted only for this minimum compatible feature scope. Do not ask again for that authorization.

## Scope

- Add schema 20 with `work_pending_instructions` and `work_agent_appearances`; do not change existing run-queue semantics or rewrite historical ledger records.
- One explicit pending instruction per Work session. Freeze exact input, requirement and task/dependency context, resolved context versions, session/source identity, generation and authorization. Keep idempotent UUID receipts and immutable input digests.
- Promote only after the exact predecessor is COMPLETED with verified successful file finalization, with a fresh baseline and revalidated identity/context. Run creation, pending consumption and receipt persistence share one transaction.
- Hold on failure, uncertainty, disconnect, drift, restart and shutdown. Never replay automatically. Stop cancels pending intent atomically; if promotion won the race, cancel its already-created child run without dispatching another turn.
- Use an independent revisioned appearance endpoint and the existing six-color palette; reset uses a nullable color row and preserves revisions. Old layout clients cannot erase a saved color. Appearance grants no execution capability.

## Ownership

Parent: migrations, shared schema module, execution service/runtime/models/API, server mounting, integration tests, documentation and commits.
`onboarding_design`: new pending-instruction panel and narrow integration in `WorkSessions.tsx`, related frontend tests.
`spatial_design`: appearance service/router, picker and WorkPage/visual integration, related tests. No migration/server edits by that agent.

## Independent API contract

GET `/api/v1/works/{work_id}/sessions/{session_id}/pending-instruction` returns `{item}`; current active record otherwise latest terminal, optional `request_id` query for exact unknown-receipt reconciliation.
POST to the same endpoint accepts StartRun fields plus `predecessor_run_id`. Its compact 201 receipt contains `pending_id`, `session_id`, `state`, `revision`, `predecessor_run_id`, `request_id`.
POST `.../pending-instruction/{pending_id}/cancel` accepts UUID and pending `expected_revision`, returning the same compact fields. Queue writes do not change the session revision.
The public item includes immutable input/context references, enqueue request ID, requirement version, state/reason, pending revision, timestamps and promoted run ID. Unknown outcomes retain the original request UUID and exact payload; no automatic resubmission.

GET `/api/v1/works/{work_id}/agent-appearances` and POST `/api/v1/works/{work_id}/agents/{agent_id}/appearance` use existing local Core authentication. Only linked agents are editable. Display revisions are independent of Work/session revisions.

## Risks and checks

Cover stop/promotion/dispatch races, simultaneous enqueue, duplicate and mismatched UUIDs, finalization failures, context version changes even if text is identical, requirement/task/identity drift, stale generations, restart and shutdown holds, immutable SQL triggers, dependency pins, old-client layout saves, color CAS/reset/restart, and stale Work callbacks. Run relevant focused tests then one independent full backend/frontend verification. Verify migration on isolated copies and the final exact commit in CI before a new native package is claimed accepted.

Rollback is an ordinary code revert while preserving added tables, history and failure evidence. Do not automatically downgrade or delete queued data from the user's database. The original Windows business data was restored before this phase; do not migrate or start it during development. No Windows restart, merge, publication, global package installation, source startup or permission widening.

The latest Computer Use attempt was stopped by physical Escape after renewed authorization. No Windows or Android UI inputs without fresh human authorization. MuMu 2 and 5 remain protected; only new instance 1 may be used if interactive testing resumes. Current tested MSI 4a8 was uninstalled according to the acceptance plan, and six original files and their independent backup matched SHA-256 after restoration.

2026-10-24 phase24 implementation complete for source validation, not final product acceptance: independentfullbackend1495passed49skipped(1544), frontend317passed/44files/typecheck/productionbuild/scopedRuff PASS. Color actualCore/SQLite/browser10checks PASS inclrestart/CAS/reset/independentrevision. Queue actualCore HTTP+SQLite repeatedPASS exactinput/onlyoncesend/realfilefinalization/Stopboth/restartHELD; localstrictbrowser blockedBROWSER_SANDBOX_UNAVAILABLE withsandboxenabled, no UI PASS claimed. Newexactreportgate validatesexercise8/reopen4 keys andliteraltrue;34gateresistance testsPASSseparatefromfullsnapshot. InitialHTTPtimeoutretainedunreproduced. NewCoreCI gate runsproductionqueuebrowser+restart onhostrunner. NoWindowsoriginalDBmigration; sixfilesrestored after4a8testuninstall. Userexplicitapprovalremainsinforce. FinalnewcommitCI/nativepackagedchecksrequired. See verification/PHASE24_LOCAL_20261024.json.
