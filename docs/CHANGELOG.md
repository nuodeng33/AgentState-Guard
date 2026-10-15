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
- A full local backend run showed failures and was interrupted after extended
  lack of progress; it is **not** a passing full-suite result. The fail-fast rerun
  finished with **858 passed, 5 skipped, 1 failed** at
  `tests/test_host_workspace_authority.py::test_distinct_agent_roots_fail_closed`:
  expected `UNAVAILABLE`, observed `BOUND`. This authority-boundary discrepancy
  remains unresolved; downstream tests were not reached in that rerun. GitHub
  Actions is an additional verification gate. No tests were disabled to make this
  source snapshot appear green.

## 2026-07-23

- Initialized AgentState Guard v0.1.0
