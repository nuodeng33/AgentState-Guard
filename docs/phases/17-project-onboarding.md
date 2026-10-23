# Phase 17: Project onboarding (2026-10-23)

Requirement: ASG-ONBOARD-001. User: complete the planned usable integration paths, including connecting an existing project.

Scope: reuse discovery registration for programs/session stores from session creation; register protected directories and configure enabled/retention/quota from session creation and file recovery. Reuse current Core API, schemas, authority and provider checks. Carry uncertain request identity across panel closure and refresh authoritative results before selecting a registered scope. A candidate is metadata, never consent or a baseline guarantee.

Non-goals: no merge/release, Windows restart, permission widening, schema/public API changes, automatic source startup, credential changes, animation redesign or unsupported provider execution implementation. Physical mobile acceptance remains independent.

Affected paths: web/src/api/{execution,protection}.ts; components/{ProtectionScopeManager,ProjectConnectionCatalog,DiscoveryCatalog,WorkSessions,WorkRecovery}; pages/WorkPage; focused tests and completion documents.

Risks: unknown mutation outcome, stale revisions, mismatched application/store/scope source, stopped sources and stale metadata. Retain exact commands/UUID, freeze conflicting actions, validate receipt identity and re-read server state. GET candidates never register or start an environment. Program/store registration retains the existing discovery interface and contract.

Tasks: [x] independent design and gate; [x] implementation; [x] regression and independent verification; [x] real Core/browser integration; [x] completion documentation; [x] commit/push and exact-commit CI.

Tests: focused enrollment failures/retry/configuration/source cases; session/recovery integration; full frontend/typecheck/production+demo build; backend full regression via deterministic JUnit parser; disposable real Core/browser first-use path and recovery configuration. Do not equate provider metadata with actual execution. Existing native provider/recovery evidence remains separately attributed.

Rollback: base 0661c24, revert only ASG-ONBOARD-001 changes. No migration or original user data mutation.

Exit: implemented UI paths tested; exact limitations and evidence recorded; changes pushed without merge/release; matching CI conclusion recorded. Native and mobile evidence cannot be invented from browser tests.
