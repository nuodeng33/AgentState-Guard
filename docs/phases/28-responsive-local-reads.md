# Phase28 — responsive local reads and owned startup

Requirement ASG-CLOSE-003, WS-007/008; user repeatedly reported long entry/loading and task details without useful controls. Existing installation/recovery/mobile/provider goal remains in force.

## Causes and implementation

- Startup previously performed foreign discovery inside create_app before binding the API. The existing protection worker now initializes sources and restores saved pause/interruption intent before starting execution/observation. Basic reads are available immediately. First reliable discovery still gates configuration-recovery writes; no guessed PRODUCT_CONFIG fallback.
- R4 async routes performed CPU-heavy full ledger validation on the event loop. They now use the existing bounded worker pool with per-worker DB ownership. All read projections run in SQLite savepoints.
- Six Agent records caused seven full ledger passes through per-Agent binding resolution. A private same-snapshot resolver shares one full validation for the projection.
- Read validation reuses only a successful exact fingerprint covering every row and all 19 typed raw authority fields. Every read still scans the entire ledger. At most64 process-local hashes are retained; no payload/projection cache, TTL, head/count shortcut or stored hash rewrite. Public binding resolution, supervision authority and Agent-attach writes retain unconditional full validation.
- Work metadata and saved layout start together; work content renders independently. Unknown layout revision stays null and layout remains read-only until its actual CAS is known. Reads do not automatically rewrite existing layouts.
- Agent reads publish independently, preserve actual prior records on failure, coalesce in-flight reads and fence client/work generations. Initial unknown count is ellipsis rather than zero.
- Namespaced sibling keys remove stale duplicate panels. Task reading shows real assignee/dependencies and shares versioned content with the card. Existing edit/Agent actions remain.
- HTTP deadlines cover response headers and JSON bodies, even ignored cancellation. Bootstrap has a12-second total budget; mutations are never automatically replayed after uncertain outcomes. SSE retains its caller signal.

## Evidence

Independent frontend421passed; backend1580passed/49skipped/0failed/errors (1629total). Each JUnit parsed separately; source hashes unchanged through verification. Typecheck, production build, Ruff and diff check passed. Historical failed CI and causal RED evidence retained.

On actual Windows with a copy of the54.5MB/34526-event acceptance DB and real background discovery: factory253ms/API327ms; work list283ms; click-to-task-body175ms; Agent panel79ms and normally closes during observation. Strict production browser workflow15checks and manual delivery/recovery/restart22checks passed. These are source-Core browser proofs, not new MSI/native pointer proofs.

Single-snapshot source Agent projection on the same immutable Windows archive decreased from21.16s to3.78s with all6records retained. Concurrent read cache cold5144/4706ms and warm2436/1776ms; cold evidence remains over the5s transport budget and is not reported as a universal subsecond Agent-read guarantee. Independent local task rendering no longer waits for it.

## Safety and remaining gates

No API schema, DB migration, old ledger hash, permission, provider/model call, global system setting or user browser profile change. Test diagnostic app closed normally; six original business files3199253bytes restored against immutable backup, DB not started. Current installed program is e311 test payload, no ASG process running. A new exact-source CI/package and native cold observation are required. Full native pointer control remains tool-limited; final packaged external-edit recovery and broader provider/physical scopes remain explicit.

Rollback: revert phase28 source/components/assets as a unit to f78d1ae; never downgrade or rewrite business data/history. Exit requires actual source gates plus final exact-package verification; whole goal is not marked complete.
