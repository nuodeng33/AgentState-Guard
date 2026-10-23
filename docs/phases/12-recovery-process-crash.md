# Phase 12 — Real recovery process termination

- Requirements: ASG-CORE-003/004, ASG-TEST-001/004. User requested closing remaining items after8d9a0a7.
- Entry: existing recovery crash tests raise SystemExit then close services; phase11 only proves normalCore restart. No OS process-death evidence.
- Scope: six process-death checks, existing native service and unmodified runtime, Windows test gate and JUnit artifacts. No product behavior, schema, UI, provider or permission change.
- Approach: parent prepares real A/B/C and durable command then closes DB; child reaches existing fault checkpoint, atomically reports actualpid/op/step, blocks with no shutdown; parent verifies committed state+bytes then kills its own process and waits. Fresh service reconciles, or ordinary runtime startup auto-reconciles. No fake adapter or success substitute.
- Cases: after_intent, after_apply, before_complete, after_compensation; automatic startup recovery; external edits after crash cause NEEDS_ATTENTION with STEP_UNCERTAIN while preserving exactbytes.
- Exit: COMPENSATED restores pre-operationA/B, unselectedC unchanged; exact original command returns sameid; repeatedreconcile adds noevents/steps/operations; SQLite integrityok; close/reopen retains result; actualSIGKILL exit onLinux. External edit must remain blocked, no silent forced restore.
- Verify: pytest tests/test_protection_process_crash.py --basetemp=<outside Git/system scope> --junitxml=<evidence.xml>; fullbackend plus projectparse-test-results; Ruff/YAML/diff checks; independent review.
- CI: Linux Core matrix automatically collects tests and now uploads perPython JUnit; Windows recovery-process job runs samefile, tauri-build requires it. Workflow configurations not remotely executed in this phase.
- Platform boundary: Popen.kill uses SIGKILL onPOSIX and TerminateProcess onWindows; local evidence onlyLinux. Recovery library process is not a packaged Core/sidecar, Tauri main, process tree, power-loss or disk corruption test. Existing workerstartup wiring is tested separately.
- Remaining: Windows actualexecution/install/upgrade/DACL/packaged crash, trueprovider and remoteCI. scripts/test-desktop.ps1 swallowed healthcheck failure is a separately recorded pendingitem, not silently consideredverified.
- Rollback: revert isolated phase commit on fix/recovery-process-crash; base8d9a0a766388b3554930a32bce729dc8f68df2be. No reset/clean/migration.

## Final evidence

- Six target cases: passed1.41s; fullbackend1349 passed/26 skipped/0failures/0errors, 75.19s, projectJUnit parser1375tests. Existing684deprecationwarnings remain.
- FullJUnit contains all six new cases. Independent verification found no blocking issues; two-fileRuff, YAML gate assertions and diffcheck pass.
- Linux trueSIGKILL, fresh service and unmodified runtime recovery; strict byte preservation and idempotent terminal events confirmed. Frontend244/UI22 remain labelled baseline evidence at8d9a0a7; no frontend edits or repeat assertions.
- Artifacts: artifacts/recovery-process/{full,targeted}.{xml,log}; no provider, external messaging, remoteCI execution or push.
