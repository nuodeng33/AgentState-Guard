# ARCHITECTURE

## Continuous directory protection (migrations 15–16)

`ProtectionService` in `agentguard/recovery/protection.py` stores independent
directory scopes, immutable manifests, batches, references, previews and recovery
journals in the existing StateDB. It uses the existing BlobStore and Evidence
Ledger. A directory does not depend on a live Agent, and Work links are explicit
user associations, never evidence of authorship or execution.

The internal `protection_history.py` module handles history projections and
retention; `protection_runtime.py` handles background scheduling. The service
remains the public business entry and coordinates recovery locks, transactions,
journals and compensation. Migration 16 adds summary projections and transactional
metadata accounting without replacing full manifests or existing tables. Scope
quotas measure logical retained space; shared database/WAL pages are reported
separately. Retention runs every five minutes, or immediately under quota pressure.

`ProtectionRuntime` owns a bounded collector, durable operation queue and startup
reconciliation. An OS worker lease prevents two collectors owning one database;
a separate OS recovery lease excludes competing execution and reconciliation.
Metadata polling runs every two seconds and complete content validation at least
every thirty seconds, plus scan time. A version describes its sampling interval,
not an atomic view of the whole directory.

`DomainRegistry` validates native, Docker and WSL identities. Foreign filesystem
operations use a fixed Python3 protocol, not caller-selected commands or UNC
write paths. WSL connection is explicit; background observation cannot start it.
The local [protection contract](CORE_PROTECTION_BACKEND.md) owns new restoration.
Legacy direct restore and transaction undo return `PREVIEW_REQUIRED`.

## R4 Evidence, Policy, and Supervision

`StateDB.transaction()` is the transaction foundation for R4 writes. It owns `BEGIN IMMEDIATE`, commit, and rollback; nested transactions are rejected. A `SupervisionService` state change performs its session read/validation, session write, and `EvidenceLedger.append(connection, event)` through the same explicit SQLite connection, so either both session and ledger effects commit or neither does.

The v3 Evidence Ledger is independent from the legacy `audit_events` log. It is append-only through database triggers. Its canonical JSON authority fields, payload digest, predecessor hash, and current hash form a tamper-verifiable chain. Discovery writes bounded, one-way evidence into that ledger; it does not infer external actions.

The v4 `supervision_sessions` table is independent from legacy transactions and legacy audit semantics. Lifecycle states include `EVALUATED`, `AWAITING_APPROVAL`, `APPROVED`, `ACTIVE`, `REJECTED`, `COMPLETED`, and `FAILED`. Terminal sessions cannot revive. `BLOCK` and `UNKNOWN` sessions cannot activate; `REVIEW` requires explicit approval; a required checkpoint cannot be bypassed.

The local policy engine is deterministic and uses `BLOCK > REVIEW > UNKNOWN > ALLOW` safety precedence. It defaults to a non-ALLOW result when evidence or domain facts are insufficient. It has no AI dependency.

## Local work records

The first [Work API](WORK_API.md) uses the same Core and `state.db` with migration
10. `agentguard/work` owns work, immutable requirements and task dependencies;
`agentguard/api/work.py` is a session-protected local router with a separate
connection per request. A work revision protects concurrent edits; a persisted
request receipt makes retries idempotent in the same transaction. These receipts
are business retry records, not a replacement Evidence Ledger. Work ownership and
assignment are local responsibility labels. No execution, safety authorization or
acceptance is inferred from creating or editing these records.

Migration 11 extends this module with immutable text artifact versions and an
independently revisioned `work_views` record. Both the relation space and multi-card
workbench save layout and viewports in this record; content remains referenced by
authoritative ID/version. View mutations validate ownership and do not change work
facts. The schema-1 extension keeps older saved layouts and retry digests compatible.
Migrations 18 and 19 add managed sessions, immutable run inputs, sequenced events,
fixed submissions and human decisions. `agentguard/execution` owns protocol
connections and dispatch; `agentguard/work/delivery.py` owns output promotion and
delivery decisions. Both reuse Work receipts, Discovery identities, Protection
versions and the Ledger. See [managed session contracts](MANAGED_SESSION_BACKEND.md).
Attachments remain future work.

Migration 12 adds `work_agent_links`: a user may associate a Work with an Agent
instance from the latest verified Discovery projection. `agent_id` is an opaque
hash of Agent type and process instance, stable across refreshes of that process;
the process instance includes domain, PID, creation time and collector, so restart
creates a new identity. The association stores bounded display facts and survives
when that Agent is absent from a later discovery. The read model then reports
`NOT_CURRENT` and an unknown lifecycle, not a claim about current execution.
Migration 13 adds `work_agent_tasks` for an explicit set of same-Work task IDs per
linked Agent. Replacing this set, including clearing it, uses the Work revision and
idempotent command receipt. Unlinking removes these task references while retaining
Agent evidence and task records. Names and legacy assignee labels never create links.
`RUNNING` is a process observation; Work activity remains `UNKNOWN` without a
separate execution record. Explicit managed execution supplies that record; no
execution or detailed output routes are added to Device Link.

The Work frontend owns a full-viewport shell separate from the Guard sidebar.
Navigation progressively reveals overview → Work core → aggregate workbench →
optional reading. Guard pages remain reachable through compact controls. The
Work component stays mounted during Guard visits so local drafts survive; its
keyboard handling and optional 3D renderer must stop while hidden. Layout saves
belong to per-Work sessions, including revision and retry payload, rather than a
single mutable global save state. Returning never waits for persistence success.
3D is loaded only for observation and reads the same Work facts, verified discovered
Agent identities and explicit Work/task links. It cannot infer execution, progress
percentages or acceptance from those links or task counts.
