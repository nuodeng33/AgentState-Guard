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

## 2026-10-29 current continuation — two concrete gaps

Pushed candidate cdd9060d10097430139451890d756330788721a9 on fix/windows-native-install. Desktop Windows40747397565, Android Build40747397562 and Android Emulator Runtime40747397673 succeeded. Core40747397594 failed one Python3.12 action-projection assertion (job123024721593); other matrix jobs cancelled by fail-fast. The validator can open a pre-POST snapshot and publish it after POST, so completed checked_at alone did not prove a post-write snapshot. Sol added uncommitted verification.started_at tied to the published pass and corrected strict waiting helpers; real SQLite latch regressions and114related tests passed. Do not call cdd's package fixed for this field.

Current actual packaged sidecar artifact12840934972, ZIP sha256 a3ee35a67ef1036d870c1bee089128bdd82a27f79c31693662e58a341f158cfd, EXE sha256 3ccf9ae452900502c7cc4f819fd41cfa441327458fd0af8a6e1b123d27ff255f. Actual512-file crash verifier failed before operation creation because preview exceeded8s. Separate diagnostic returned200 in8.735s; source cProfile6.735s traced repeated target blob hydration/put reads. These are failures/diagnostics, not crash acceptance. Sol now owns minimal storage retain_verified/protection preview optimization and relevant tests; Terra owns exact disk-work POST60s deadline plus explicit preview progress, keeping all GET/other POST5s. Full integrity, rollback, refcounts, GC dependencies and actual-restore revalidation must remain.

Windows current stage: C:/Users/Administrator/Documents/ChatGPT/New project/asg-native-20261023/takeover-20261029. All evidence stays there. Original AppData six files remain original and matched independent backup; not launched. Old installed sidecar SHA88eb0c1cdc541ebd20be88265ebbf3f9be724da0ee600c634d391bd6f4ee88bd seeded real Work/baseline/SUCCEEDED recovery under upgrade-v3/data. seed-report.json PASS, OwnedJob active0; check after final MSI: python upgrade-phase.py check <new-installed-sidecar-sha> upgrade-v3. Existing scripts safely own only created job processes and capture stdout; direct no-stdio invocation failed and remains in upgrade/upgrade-v2.

MuMu1 (creation1792756085489560, nameASG-验收-20261023) started; latest observedADB127.0.0.1:16416 (always resolve manager info again). Only its ASG testapp opened, current oldapp unpaired (mobile/before-final-package.xml/png). MuMu2/5 and other windows untouched. New native MSI/QR/mutualSAS/offline/reconnect/restart/revoke tests remain. Desktop Tauri capture/input not yet attempted on this candidate.

Provider boundary independent read-only work: Docker Codex0.147 bwrap namespace denied, Kimi0.29.2 has no enforced scope implementation. Ubuntu was explicitly authorized and started; actual bundledLinuxCodex0.164.0-alpha.11.1 sandbox probes under isolatedCODEX_HOME passed internal-write/external-refusal and read-only/network-refusal canaries. This is not model/app-server acceptance. Native server-owned proof binding and current app-server policy ACK adaptation remain implementation gaps. No permission relaxation or original credentials read.

Next: finish storage/UI fixes, Windows same512-file latency/actual crash tests, final precise commit+CI/packages, direct upgrade and nativeWindows/MuMu1 flows, then provider scope implementation/actual managed runs. Broad goal remains active; no release/merge/full completion claim.

## Final source candidate before new package

The precommit snapshot race is fixed by started_at metadata; all test waits require a pass begun after their causal boundary. Real restore previews retain each historical target only after one complete read/decompression/hash, remove the early repeated hydration, and keep raw-current put repair. undo_operation protects only its exact source operation during that one retention pass; independent preview references then own content and the source remains collectible. Negative corruption/refcount/transaction/GC/actual-execution tests passed.

Exact3disk POSTs (preview, scope registration, capture) now have60s bounded whole-response deadlines; all GET/other mutations remain5s. Preview pending has visible bilingual status; no automatic mutation resend. These budgets accommodate real work without relaxing integrity. They are not a claim of instant512-file work.

Final local backend1616passed49skipped0failures/errors,1665JUnitcases197.61s; project parser verified /tmp/asg-phase31-final.xml. Final frontend448passed55files, production tsc/build and diff check passed. Windows exported product hashes still exactly match current code. Source profile target blob reads reduced from1536 to1024; total5.875s under concurrent validation (baseline6.735s from separate run, not a controlled benchmark). Actual productionChrome UI selected512distinct files via real checkboxes/pages, showed pending thenREADY/Confirm, preview HTTP2006119.45ms, health2008.49ms andWork20019.89ms during preview. No restore sent, actual file bytes unchanged, no page errors, Core/Chrome closed normally. [Machine evidence](../verification/RECOVERY_PREVIEW_SOURCE_20261029.json).

Next exact package must cover these changes; cdd9060package does not. Real native MSI input/upgrade/MuMu pairing and actual packaged interruption/compensation/external-edit preservation remain open; provider proof implementation remains separate.
