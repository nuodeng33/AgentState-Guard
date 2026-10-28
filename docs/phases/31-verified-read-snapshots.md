# Phase 31 — Last-verified interface reads

Requirement: the user approved displaying the most recent verified state and its time, with background full verification; linking, approval and recovery must still perform current full verification. Current takeover also requires final function completion and actual native testing. No previous PASS label proves this new candidate.

Base: 28cc7b0fa647f8c1cbe8ae21192d37e4a3494811. Rollback: this commit, preserving user data and existing uncommitted report mode changes.

Entry evidence: phase30 recorded O(history) verification on every Agent read with 34,526 ledger events. The confirmed architecture change was not implemented. Four new frontend tests fail against the baseline (13 existing pass), log /tmp/asg-phase31-ui-red.log.

Scope: background consistent full verification; dated read snapshots; explicit cold, stale, invalid and transient-error states; atomic publication and bounded worker lifecycle; current full verification on every mutation. Existing Work relations, facts and original ledger remain authoritative. No security relaxation or loss of historical checks.

Ownership: Sol/medium owns backend reader/API/tests; primary owns frontend, phase record, integration and native acceptance. No other file writers.

Frontend contract: optional verification {state, verified_at, checked_at, refreshing, reason_code}. No snapshot is labelled empty discovery. Invalid/error remove current observations while retained manual associations remain readable. VERIFIED/STALE snapshots allow selection only while the observation itself remains current; the existing server mutation performs full current verification. Definitive integrity/currentness rejection is distinct from an unknown receipt. PENDING/INVALID/ERROR cannot supply new candidates.

Exit conditions: relevant failure regressions, full applicable suite/build, current CI and packages, actual Windows native input/upgrade and recovery, MuMu1 actual final APK flow, original-data preservation. Physical-device/provider environment gaps remain separate until real evidence is available. Not yet complete.

Verification: web npm test and npm run build; backend .venv/bin/python -m pytest tests/ -q. Applicable CI: Core, Desktop Windows, Android Build, Android Emulator Runtime.

## Source integration evidence

All five interface projections use the same dated read contract. A single pinned full historical verification feeds fast projections; fast results publish before recovery coverage. Recovery coverage retains its original additional safety checks. Writer/AI/evidence authority never uses the display cache. Worker sleeps five seconds after completion; GETs cannot launch duplicate scans. Read validation is cancellable while waiting for the cache lock and inside Python row loops. Recovery's existing in-flight file reads and extra writer validation naturally finish; no forced thread termination is claimed.

Final backend targeted integration:135passed. Full frontend:438passed/55files; a subsequent narrow stale-action/rejection update passed both complete WorkAgents suites (30tests) and rebuilt production assets. Full backend currently running; no combined/full final pass inferred.

Actual Windows source candidate ZIP SHA256 67ef4a3ebc5afccdd79cc2a39b39bcd22e7e487f69f1b5ae1c54d8a7ecfee355; export product hashes rechecked unchanged after measurement. On the isolated34,526event archived copy, cold concurrent Agent read1087.245ms returned PENDING without waiting for full verification. Background automatically published VERIFIED; subsequent runtime/supervision/changes/recovery reads2.512–3.419ms. Production sandboxed Chrome click-to-body256.18ms and exact body hash matched; normal Core exit0 and archive unchanged. This is Windows source/browser evidence, NOT MSI/native pointer acceptance. See [machine report](../verification/VERIFIED_READ_SOURCE_20261029.json).

Original AppData six files exactly match the independent original-backup by relative path, size and SHA256 before native work. They have not been started or migrated. Windows Computer Use window enumeration succeeded; no original-data app launch yet.

## Source gate closure

Full backend second run:1599passed/49skipped/0failures/errors (1648JUnit cases,156.99s), single-report parser verified. First full run retained14failures due old synchronous test expectations; replacement waits require a new checked_at and, for valid snapshots, a new verified_at. They preserve business facts and rejection assertions. Slow-read regression retains actual worker/event-loop thread distinction, adds immediate PENDING and healthy Work/health while the validator is blocked, and waits for real verified results after release. Product was unchanged by these test corrections.

Windows actual source Core/normal sandboxed production Chrome manual workflow passed all22top-level checks: Work delivery, read-only previews/diffs, selective restore, undo, stale preview rejection, actual byte preservation and restart persistence. [Workflow evidence](../verification/VERIFIED_READ_WORKFLOW_20261029.json). This remains separate from installed MSI/native-pointer and real-provider acceptance.

Next: commit exact candidate, run exact-commit build/CI, install and inspect native Windows/MuMu1 final packages; finish real provider and crash-recovery boundaries. An old-installed-Core isolated seed health timeout is retained under Windows takeover-20261029/upgrade; no original AppData started, Terra investigating. No MSI/native completion claimed.
