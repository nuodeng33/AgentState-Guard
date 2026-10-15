# Local Work API — persistent content and spatial views

Implemented 2026-09-27 on the Host-native development baseline. This extends the
existing Core; it does not introduce another service, database, security approval
system. Agent associations were added 2026-09-29; managed execution and fixed
delivery decisions were added 2026-09-30. See [managed session contracts](MANAGED_SESSION_BACKEND.md) and
[architecture](ARCHITECTURE.md).

## Implemented scope

- Create/list/edit a work, with a local owner label.
- Preserve full requirement text as immutable versions, readable by version.
- Create/edit/read tasks, full task bodies, assignee labels, and same-work dependencies.
- Preserve data after reopening the service; migrations 10-13 upgrade the existing state DB.
- Save immutable text artifact versions tied to an exact requirement version.
- Save workbench cards, reading positions, relation-space layout and both viewports.
- Reject cycles, foreign/missing task references, stale edits and unknown request fields.
- Commit each write and its retry receipt atomically. A request UUID may be retried
  with the same operation, target and payload; reuse with different content returns 409.
- Link a verified, currently discovered Agent instance to a Work, then explicitly
  assign zero or more of that Work's tasks to it. These are user associations.

Owner/assignee are responsibility labels, not authenticated multi-user identities.
An assignee label never identifies or links an Agent. Agent process lifecycle and
Work activity are separate facts: even `RUNNING` describes the observed process,
not execution of this Work or any task. Work activity remains `UNKNOWN`.
Managed runs have separate protocol-based execution states. Fixed delivery sets,
review and owner acceptance are available through the managed session contracts.
Artifacts now distinguish MANUAL, AGENT_RUN and USER_EDITED_RUN_OUTPUT sources;
their existence does not establish acceptance. Attachments remain a later slice.
No work routes are added to Device Link. The existing Core session middleware applies.

## Requests and responses

All writes require a fresh UUID `request_id`. Generate it once per user action and
reuse it only for retries. Edits also require `expected_revision`, taken from the
work read response. Any successful mutation increments the work revision, including
task changes; a retry returns the original receipt and does not increment it again.

| Method | Path under `/api/v1/works` | Body / result |
|---|---|---|
| POST | empty | `request_id`, `title`, `owner`, `requirement`; returns `work_id`, `revision` |
| GET | empty | `limit` 1–100 (default 50), `offset`; returns `items` |
| GET | `/{work_id}` | Work metadata, current requirement body and task summaries |
| PATCH | `/{work_id}` | `request_id`, `expected_revision`, `title`, `owner` |
| POST | `/{work_id}/requirements` | `request_id`, `expected_revision`, `body`; adds an immutable version |
| GET | `/{work_id}/requirements/{version}` | Exact version body and creation time |
| POST | `/{work_id}/tasks` | `request_id`, `expected_revision`, `title`, optional `body`, `assignee`, `depends_on` |
| PUT | `/{work_id}/tasks/{task_id}` | Same fields; replaces task content/assignment/dependencies |
| GET | `/{work_id}/tasks/{task_id}` | Full task body, assignment and dependencies |
| GET | `/{work_id}/agents` | Linked Agent items with `task_ids`, current presence and discovery status |
| POST | `/{work_id}/agents` | `request_id`, `expected_revision`, `agent_id`; link a currently discovered Agent |
| POST | `/{work_id}/agents/{agent_id}/tasks` | `request_id`, `expected_revision`, `task_ids`; replace this Agent's task set, including `[]` to clear |
| POST | `/{work_id}/agents/{agent_id}/detach` | `request_id`, `expected_revision`; unlink the Agent and its task references |
| POST | `/{work_id}/artifacts` | `request_id`, `expected_revision`, optional `artifact_id`, `requirement_version`, `title`, `body`; returns exact artifact ID/version |
| GET | `/{work_id}/artifacts/{artifact_id}/{version}` | Immutable text, title, requirement version, creation time and explicit source/provenance |
| GET | `/{work_id}/view` | Independent view `revision` and saved `state`, or revision 0 / null state |
| POST | `/{work_id}/view` | `request_id`, view `expected_revision`, `state`; returns new view revision |

Work, task and request identifiers are UUIDs. `agent_id` is an opaque discovery ID.
Mutations return a receipt (`work_id`, `revision`, plus the
created `task_id` or `requirement_version` where applicable). Read after mutation to
refresh the displayed state. A task PUT and Agent task POST are replacements, not
partial updates. The latter also returns the assigned `task_ids`.

`GET /api/v1/agents` supplies current candidates from the verified Evidence Ledger.
Each candidate exposes `agent_id`, `detected_identity`, `role`, `lifecycle`,
`workspace`, `observed_at`, `evidence_refs`, `uncertainty`, `association: null`,
`activity: UNKNOWN`, and `presence: CURRENT`. Legacy evidence without a matching
opaque ID remains readable but cannot be linked. The ID hashes Agent type and process
instance; the process instance uses domain, PID, creation time and collector. It is
stable across discovery refreshes for the same process and changes when that process
restarts. The display name is not unique and is never used as a binding key.

Linked Work items have `association: USER_LINKED`, `task_ids: []` or explicit task
UUIDs, and `activity: UNKNOWN`. When a linked instance is absent from the latest
discovery, its association remains visible with `presence: NOT_CURRENT`,
`lifecycle: UNKNOWN`, `uncertainty: true`, and reason `WORK_AGENT_NOT_CURRENT`.
This does not assert that the process is idle, stopped, or executing the Work.

```json
{
  "request_id": "21185fb1-0633-430e-802a-f11684ba97d8",
  "title": "整理真实交付",
  "owner": "本地负责人",
  "requirement": "保留原始要求、正文和后续修改历史。"
}
```

Limits: title 200 characters; owner/assignee 120; each body 100,000; 500 tasks per
work and per Agent task set; 100 dependencies per task. Bodies retain whitespace.
Labels cannot be blank.
Existing credential detection rejects recognized secret patterns before storage
(`WORK_CONTENT_SENSITIVE`), without echoing content or silently changing it.
This heuristic is not an encrypted secret store and cannot identify every secret.
Work text is neither sent to AI nor exposed through remote projections.

Stable errors: `WORK_NOT_FOUND`/`WORK_TASK_NOT_FOUND`/`WORK_REQUIREMENT_NOT_FOUND`
(404), `WORK_REVISION_CONFLICT`/`WORK_REQUEST_REUSED` (409),
`WORK_REQUEST_INVALID`/`WORK_DEPENDENCY_CYCLE`/`WORK_DEPENDENCY_NOT_IN_WORK`/
`WORK_DEPENDENCY_DUPLICATE`/`WORK_TASK_LIMIT`/`WORK_CONTENT_SENSITIVE` (422),
`WORK_STORAGE_UNAVAILABLE` (503). Invalid requests expose field locations/types,
not submitted bodies. A stale editor must read the current revision before
resubmitting a consciously merged edit; do not silently replace its revision.
Agent association adds `WORK_AGENT_LINK_NOT_FOUND` (404),
`WORK_AGENT_ALREADY_LINKED`/`WORK_AGENT_EVIDENCE_INVALID` (409), and
`WORK_AGENT_NOT_CURRENT`/`WORK_AGENT_TASK_DUPLICATE`/
`WORK_AGENT_TASK_NOT_IN_WORK` (422). A task must belong to the same Work; several
Agents may be associated with one task. Unlinking removes only the Work association
and its task references, never the Agent evidence or task record.

## Persistence and verification

Migration 11 adds `work_artifacts`, immutable `work_artifact_versions`, and
`work_views`. View writes use their own revision and do not increment the work
revision or alter task dependencies. The stored state uses schema version 1:

- `viewport`: workbench `{x, y, zoom}`; `cards`: up to 40 content references with
  kind, ref_id, version, x/y, width/height and independent body scroll positions.
- Optional `space`: its own `viewport` and up to 1000 `nodes` with kind, ref_id,
  version and x/y. Nodes and cards reference the same authoritative entities.
- Optional `surface`: `space` or `board`. Older state without these fields remains
  readable and retryable; the UI initializes the relation space for that state.

Viewport zoom is bounded to 0.25–2; positions to ±100,000. References must belong
to this work and name an existing exact requirement/artifact version. Task refs
have null version and read current task content. Duplicate references within a
surface are rejected. A reference may appear in both space and workbench.
The Work core uses the work's requirement reference; responsibility labels never
create Agent identities or execution edges. Explicit Agent links do not create
execution edges either. Closing a card removes only a view reference.

`WORK_VIEW_CONFLICT` (409) preserves the other window's saved layout. Re-read and
explicitly discard local layout, or retry a failed identical request; never replace
the revision silently. Invalid/missing/cross-work references and duplicate nodes
return `WORK_VIEW_REFERENCE_INVALID` / `WORK_VIEW_DUPLICATE` (422). Artifact
lookups return `WORK_ARTIFACT_NOT_FOUND` (404) for inaccessible versions.

Migration 10 adds `works`, `work_requirements`, `work_tasks`, `work_requests`.
Requirement triggers reject UPDATE/DELETE. The work service owns its transaction;
each synchronous request owns a separate SQLite connection. Task graph validation,
version checks, writes and retry receipts share that transaction. Reads return
coherent snapshots. No schema change is made to the Guard domain tables.

Migration 12 adds `work_agent_links` for verified Agent identities and the display
facts observed at link time. Migration 13 adds `work_agent_tasks`, keyed by Work,
Agent and task. Attach, task replacement and detach use the same work revision,
transaction and retry receipt contract as other Work mutations. Reading a linked
Agent rechecks the latest verified discovery; old links retain their last observed
identity without being promoted to current presence. Neither migration expands
Device Link or introduces an Agent execution adapter.

`tests/test_work_api.py` exercises actual Core routes: restart/read-back, historical
requirements, retries, simultaneous stale editors, cycles/foreign references,
session protection, sensitive content rejection, transaction rollback and v9 upgrade
preserving an existing Guard checkpoint. Tests use temporary state, not user data.
They also cover verified Agent binding, historical presence, task-set replacement,
cross-Work rejection, idempotency and unlink cleanup.

## Desktop packaging changes

`scripts/build-desktop.ps1 -Python <project-python.exe>` requires an existing
environment with application dependencies and PyInstaller, plus Node/npm and
Rust/Tauri tooling. It builds frontend → new sidecar → Tauri and checks each exit
code. A unique build directory prevents stale sidecar reuse. Generated provenance
source is restored even on failure. The release directory receives the new sidecar.

The optional `-Release` requires clean source. A dirty development build retains
`source_sha` and `source_dirty` in its build report but embeds no exact runtime SHA.
The script does not install global tools. `tests/test_desktop_build_script.py` checks
staging and failure behavior with stub tool processes; this is not a real MSI build
or install test. No packaged runtime was launched during this implementation.

## Windows recovery compatibility

Workspace logical paths use POSIX syntax regardless of host OS; legacy host paths
keep their existing handling. Native DACL restoration preserves protection flags.
Permission comparison tolerates only the `AI` propagation marker added by Windows,
while retaining ACE order, access masks, trustees, inheritance entries, protection
and request flags. Stored proof bytes/digests remain unchanged and tamper-checked.

This distinction follows Microsoft's [automatic inheritance behavior](https://learn.microsoft.com/en-us/windows/win32/secauthz/automatic-propagation-of-inheritable-aces)
and [security descriptor control flags](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-descriptor-control).
Native Windows regular-file tests cover protected/unprotected permission roundtrips;
workspace API tests cover isolated test restore, actual restore and quarantine failure.
