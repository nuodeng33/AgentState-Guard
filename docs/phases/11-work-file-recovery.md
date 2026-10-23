# Phase 11 — Work file recovery

- Requirements: ASG-GUI-004/005, ASG-TEST-002/003, WS-007/008. User approved implementation from 4feb030 and explicitly selected all protected directories.
- Scope: Work menu recovery region; global directory/version/file selection, real preview and plain-text diff, explicit restore, global history and previewed undo. Reuse existing HTTP contracts and in-memory session authentication.
- Non-goals: new directory registration, protection policies, provider execution, Windows release, remote push/merge, schema changes.
- Files: web recovery API/component/styles/tests; WorkPage/menu copy and integration tests; real local workflow verifier; shipped production assets and governance docs.
- Data flow: scopes + global operations -> version full pages of 50 -> client file pages of 50 -> exact file IDs preview -> digest-bound confirmation -> idempotent command -> durable result polling. Undo reads operation by ID and derives file IDs from step before/after entries.
- Boundaries: preview is not execution; no preselected files; unselected files stay intact; unknown receipt preserves command in memory and allows only same-command retry; hidden UI stops polling but not the operation. No session token or preview credentials persisted to browser storage.
- Risks: stale preview, duplicate submission, late responses, unavailable history, StrictMode remount, mixed global operation kinds, mobile clipped confirmation. Tests cover strict identity, invalidation, lock, epoch guards, history paging, explicit state labels and viewport accessibility.
- Verification: component/integration tests, typecheck, production/demo builds, actual Chromium/Core/file UI roundtrip, related backend/full suite as relevant, independent review. Exact results recorded on completion; no counts assumed.
- Rollback: revert the isolated phase commit on feat/work-file-recovery; base 4feb030775decda9ab8ad9e5514fade42e77c6de. No reset/clean or database migration.
- Exit: browser UI restores selected A/B leaving C intact, previews and confirms undo, displays durable history after restart; stale/expired preview cannot silently execute; all checks pass within stated evidence scope. Windows and provider gaps remain explicit.

## Final verification

- Backend: 1343 passed / 26 skipped / 0 failures, JUnit 1369 cases audited by scripts/parse-test-results.py; 78.51s. Existing FastAPI deprecation warnings remain (684).
- Frontend: 244 passed / 36 files, 37.58s; no test stderr warnings. Recovery component27 and entry4 are subsets of this run, not additive test totals.
- Production/demo builds and typecheck pass; production web_static rebuilt. Existing 3D >500kB chunk advisory remains.
- Actual Core/Chromium report 90e0f474-c651-4a9a-adcd-946bbe84f94b: 22/22 required checks, four browser phases with no page errors. Recovery exercise35 and reopen10 actual API requests; no interception or model calls.
- Expiry injected only into one disposable preview row: actual confirmation rejects with 409 / PROTECTION_PREVIEW_EXPIRED and leaves A/B/C unchanged. Not a five-minute wait or production hook.
- 390x844 screenshot confirms scrollable confirmation and diff. Normal Core restart followed by a fresh browser proves persisted history, not crash recovery or automatic reconnection.
- Independent design/entry review and verification review have no remaining blocking findings. Scoped Ruff, Node syntax and diff checks pass. Remote CI and Windows/provider acceptance remain outstanding.
