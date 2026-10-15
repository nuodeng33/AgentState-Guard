# Changelog

## 2026-10-15 — v0.9.0.dev0 source preview

This entry brings the accumulated September workspace/core work and October fixes
into the repository. It does not announce a package or installer release.

### Added

- Work, immutable requirement/result versions, tasks, dependencies, agent and
  directory associations, fixed submissions, reviews and owner acceptance.
- 2D relation space, aggregate workbench, focused reading, saved spatial views,
  and an optional 3D factual observation view.
- Automatic program/instance/session-store discovery with shared source identity,
  bounded session reading and explicit unavailable/partial/stale states.
- Continuous directory protection, version history, diffs, selective restore,
  compensation, retention accounting and restart reconciliation.
- Managed session/run/event records and native provider adapters; verified Windows
  Codex execution, resume, output, cancellation and delivery. Other environments
  remain gated by their actual verified capabilities.
- Work-level execution attention, exact-run navigation, explicit versioned
  context selection and confirmation when requirements change.

### Fixed

- Preserve buffered output when a managed connection is lost; uncertain runs do
  not automatically resend input or become successful.
- Use deferred read snapshots for Work projections so parallel card reads do not
  reserve SQLite's write lock. Existing command transactions remain immediate.
- Restore question visibility after closing/reopening its panel, preserve input
  drafts, and distinguish content-load error categories.
- Consolidate old restore write paths, harden blob integrity/retention and Windows
  workspace/build provenance handling.

### Development and limits

- Append schema migrations through version 19; retain existing Work/evidence IDs.
- Add source/cloud instructions and keep runtime databases, credentials, reports,
  validation bundles and model transcripts outside Git.
- Historical native/multi-environment results and remaining limitations are in
  [execution acceptance](MANAGED_SESSION_ACCEPTANCE_20260930.md),
  [protection acceptance](CORE_PROTECTION_ACCEPTANCE_20260929.md) and
  [discovery acceptance](DISCOVERY_ACCEPTANCE_20260930.md). Local reports referenced
  there are not included in this public source snapshot.
- Multi-Agent orchestration, unverified cross-environment execution, production
  installers and current CI status are not declared complete by this update.

### Source-sync verification (2026-10-15)

- Frontend: 199 tests passed; current TypeScript/product build and the portable
  feedback browser script passed (explicit fixture API, no model calls).
- The earlier 93-test Work/transaction/execution regression remains dated evidence
  in the acceptance notes. New source-sync spot checks: 32 passed, 1 platform skip.
- Correction: the initial full run and its **858 passed, 5 skipped, 1 failed**
  fail-fast rerun used `--basetemp` inside this Git checkout. Synthetic directories
  inherited the checkout's Git root, invalidating workspace-authority fixtures.
  The same authority/optimization tests pass outside the checkout; a fresh full
  run in system temp completed with **1304 passed, 12 skipped**. Production
  authority rules and assertions were not relaxed. Tests now reject a repository-
  local `--basetemp` before executing.
- GitHub Core CI separately failed during collection because direct HTTP tests
  import `httpx` but the dev extra declared only `httpx2`. Both test dependencies
  are now explicit. Core workflow concurrency supersedes older runs for the same
  branch without disabling checks or changing notification preferences.
- A brand-new dev virtual environment passed `pip check`, collected all **1316**
  tests, and passed **78** API/Work/execution tests, including the two modules
  previously blocked by the missing import. This is local Windows validation of
  a fresh dependency install; the Linux/Python CI matrix is verified separately.
- Cloud rerun results remain separate from the local full-suite result. No tests
  were disabled to make this snapshot appear green.
- The first post-dependency Linux run reached all tests and exposed a PATH
  inventory fixture without POSIX execute permission (1298 passed, 17 skipped,
  1 failed on Python 3.12). The fixture now sets executable mode explicitly;
  a POSIX negative test also verifies ordinary non-executable files stay excluded.
  Production discovery rules remain unchanged.
- Desktop Windows smoke logs showed successful API checks, MSI startup and
  cleanup followed by exit 1: the Actions PowerShell wrapper propagated a stale
  native cleanup exit code. Each smoke step now explicitly succeeds only after
  all assertions; exits are outside `finally`, so primary/cleanup failures remain
  failures. Real PowerShell cleanup-block regression and packaging checks:
  **14 passed**. Same-branch desktop runs now supersede stale runs; actual cloud
  sidecar/MSI validation remains separate from these isolated checks.

## 2026-07-23

- Initialized AgentState Guard v0.1.0
