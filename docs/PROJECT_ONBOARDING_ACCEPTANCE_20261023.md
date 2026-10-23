# Project onboarding acceptance — 2026-10-23

ASG-ONBOARD-001, base `0661c24`. This connects existing backend capabilities to ordinary first-use UI. No backend API/schema/permission changes. No merge, release or Windows restart.

| Path | Result | Evidence |
|---|---|---|
| Session creation → program/session-store registration | PASS | Explicit environment scan, real executable and empty store registered through actual Core/browser |
| Session creation → directory registration | PASS | Explicit registration, server receipt then authoritative scopes refresh, selected new scope |
| Protection policy | PASS | Enabled/pause, 45 days/512 MiB settings persisted; default registration remains 30 days/2048 MiB |
| Explicit session consent | PASS | Directory registration never checks consent or starts a run; Create becomes available only after consent |
| Session creation | API receipt PASS; provider execution blocked | Actual POST returned201. Linux provider connection became UNAVAILABLE / EXECUTION_PROVIDER_SCOPE_UNVERIFIED; no model execution was sent |
| Recovery → directory settings | PASS | Same scope/policy selectable; pause saved; stale recovery previews invalidated on settings change |
| Restart persistence | PASS | Core stopped/restarted; UI shows saved policy, no mutation on reopen; disposable original file unchanged |
| Chinese/mobile viewport | PASS | Chinese controls operated; 390×844 viewport has no horizontal page overflow. This is not a physical-phone result |
| Work/delivery/recovery regression | PASS | Existing real Core/browser22 checks: actual work/artifact/review/acceptance, restore/undo/expired-preview refusal/history/restart |
| Native Tauri / physical phone for new UI | Not verified | Native capture/interaction limitations documented in prior installation report; no physical phone attached. Previous native Environment no-console confirmation remains attributed to4378c6e |

Two real UI harness failures are retained separately from final successful evidence: an inaccurate Playwright label selector (replaced with observed combobox role) and strict boolean assertions against SQLite integer booleans plus an English-region locator after language change. Initial test compiler `exact` option and Python import-order Ruff failures also retained. No product assertion or authority checks were relaxed.

Real acceptance exposed a product gap: the UI blocked NEW sessions from EMPTY/DEGRADED stores that Core explicitly accepts. Both disabled-state and submission guard now use the backend-supported statuses and still enforce source equality, active protection, exact continuation binding and explicit consent.

Full frontend270 tests (38 files); full backend1360 passed/46 skipped (1406 cases, deterministic JUnit parser). Windows/PowerShell/live-specific backend cases are skipped on Linux and retain separate native evidence. Typecheck, production/demo builds, new Python Ruff and diff check pass. Independent design and final verification performed per repository workflow.

Commands:

```sh
.venv/bin/python -m pytest --junitxml=/tmp/asg-onboarding-backend.xml
.venv/bin/python scripts/parse-test-results.py /tmp/asg-onboarding-backend.xml
cd web && npm test && npm run typecheck && npm run build && npm run build:demo
.venv/bin/python scripts/verify_project_onboarding.py --program /path/to/existing/codex --report-dir /tmp/onboarding-report
.venv/bin/python scripts/verify_local_workflow.py --report-dir /tmp/local-workflow-report
```

Browser commands use PLAYWRIGHT_MODULE and CHROME_PATH supplied by the existing isolated test runtime. The onboarding harness uses temporary directories, stops only its owned Core/browser and never reads credentials. Consolidated results: [JSON](verification/PROJECT_ONBOARDING_20261023.json). Remote exact-commit results will be appended once available. Rollback: revert ASG-ONBOARD-001 UI/scripts/docs/static assets; no migration rollback required.
