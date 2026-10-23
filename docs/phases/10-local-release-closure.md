# Phase 10 — Local release closure

- Requirement IDs: ASG-TEST-003/004; WS-007/008; no requirements changed.
- Entry: bilingual `03f4bbc`, frontend 213 tests and production/demo builds passed.
- Scope: real browser/Core/SQLite manual delivery; native file recovery API; trustworthy live report validation; CI wheel gate; current readiness record.
- Non-goals: provider/model execution, Windows MSI/DACL, new restore UI, automatic browser reconnection, remote push/merge.
- Affected files: scripts/verify_{managed_session_live,local_workflow}.py; scripts/verify_local_workflow_browser.cjs; tests/test_{live_acceptance_reporting,local_workflow_verifier}.py; .github/workflows/core.yml; governance/development/readiness docs.
- Risks: false green, stale evidence, lost data or accidental model request. Mitigations: strict required checks, RUNNING/FAIL report lifecycle, isolated temporary directory outside Git/system scopes, no executor in local workflow, explicit live opt-in.
- Tests: full backend pytest/JUnit; scoped Ruff; actual Chromium/Core workflow; report failure/missing-check/interruption regressions; YAML dependency inspection.
- Exit criteria: all local checks pass with exact scope documented; independent review; remote and Windows gaps remain explicit.
- Rollback point: 03f4bbccc8a9f7a50b7584f52eb7481890fd6e07. Revert only the isolated phase commit; no reset/clean or schema migration.
- Evidence: artifacts/local-workflow/report.json, browser-create.json, browser-reopen.json and screenshots; full backend /tmp/asg-closure-full.xml and its project parser output.
- CI: local-workflow needs test/frontend; wheel also requires local-workflow; job uses real Core and retains evidence even on failure. Configuration has not yet run remotely.

Final local result: 1341 backend passed / 26 skipped (1367 collected, JUnit audited); 41 targeted report regressions; actual workflow 12 required checks PASS. Scoped Ruff, Node syntax and YAML gate assertions pass. Independent review has no remaining blocking findings. Frontend baseline remains 213 tests at 03f4bbc; no new provider or remote CI claim.
