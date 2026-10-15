# Core Directory Protection Backend

## Authority and persisted objects

Migration 15 extends the existing StateDB, Evidence Ledger and BlobStore. Migration 16 adds stored version/batch summaries, logical metadata accounting and a persisted collection interval; it backfills existing protection history without changing version manifests, digests or availability. These migrations preserve Work, Agent links, checkpoints and existing evidence. A protection scope binds a source, physical directory identity, canonical path and connection epoch. Agent process identity, provider-store session identity, user Work links and file attribution remain distinct. Agent restart never inherits a process identity; missing session cwd stays unknown. Directory capture continues without an Agent.

The implementation has four concrete boundaries: the API validates requests, `ProtectionService` owns version/recovery policy and transactions, `DomainRegistry` binds environment identities and transports, and the fixed helper performs bounded filesystem actions. Windows uses the existing native scanner for its permission model; Docker and WSL share the same POSIX helper. Existing Work, Ledger and BlobStore remain the owners of tasks, evidence and bytes. No separate task engine, provider plugin framework or parallel rollback service is introduced. Unreachable legacy live-write implementations have been removed; their public compatibility entry points reject direct restoration.

Work links use `/api/v1/works/{work_id}/protection-links` and `/protection-changes`. Multiple Works share one scope/version stream; one Work can link multiple scopes. Links use Work revision checks and idempotent request IDs. Manual association is `USER_LINKED`; observed file authorship remains `UNATTRIBUTED`. Recovery-generated entries alone may say `ASG_RECOVERY`.

A repeated physical directory is reused. Overlap errors return the existing scope ID and protected root. Replacing a directory requires registration with `replaces_scope_id`; the old scope becomes `RETIRED`, its previews expire, and its historical content remains readable. Old authority cannot restore into the new entity. A verified move of the same entity reuses its scope and invalidates old previews.

Core storage itself cannot be registered as a protected directory. If Core storage is inside a project, it must be under an excluded directory such as `.agentguard`, so capture does not recursively record its own database and versions.

## Local API

Every route below is behind the existing loopback session token. These routes and file bodies are not exposed through Device Link or automatically included in AI context.

| Route under `/api/v1` | Behavior |
|---|---|
| `GET discovery/sources` | Current and retained source observations, independent observation/connection/protection status |
| `POST discovery/sources/{id}/start-and-connect`, `/connect`, `/disconnect` | Durable source operation, HTTP 202; body has `request_id` |
| `GET discovery/sources/{id}/sessions` | Bounded provider-store metadata; never evidence of a PID executing a task |
| `GET protection/scopes/candidates` | Sources and observed session cwd candidates, paged by `limit`/`offset` with `has_more`; explicit registration still required |
| `POST protection/scopes` | Register `source_id`, `path`, `request_id`, optional `replaces_scope_id`; return `scope_id` and revision |
| `GET protection/scopes` | Scope health, coverage, observation age, storage usage, evidence and collector limits |
| `POST protection/scopes/{id}/configure` | Enable/pause and retention/quota policy with expected revision |
| `POST protection/scopes/{id}/capture`, `/collect` | Bounded synchronous capture / retention collection; body `{}` |
| `GET protection/scopes/{id}/versions`, `/changes` | Paginated versions and batches; `view=summary` omits file/entry details, `view=full` is the default |
| `GET protection/scopes/{id}/files/{file_id}/history`, `/diff?before=...&after=...` | Paged single-file history, bounded UTF-8 text diff or binary summary |
| `GET protection/scopes/{id}/versions/{version_id}/files/{file_id}` | Authorized retained file bytes as `content_b64` |
| `POST protection/scopes/{id}/versions/{version_id}/pin` | Pin/unpin available content |
| `POST protection/scopes/{id}/checkpoints/{checkpoint_id}/import` | Revalidate a native V3 checkpoint and import pinned history; no live writes |
| `POST recovery/previews` | Select scope, mode and file IDs; return bound digest, expiry, selection and affected Works |
| `GET recovery/previews/{id}/files/{file_id}/diff` | Current-to-target preview diff |
| `POST recovery/operations` | Queue `{preview_id,digest,request_id}`, HTTP 202 |
| `GET recovery/operations`, `/{id}` | Durable result; the list accepts `view=summary` for counts without step/evidence detail, while full detail remains the default and is available by ID |
| `POST recovery/operations/{id}/reconcile` | Reconcile interrupted restore; refuses concurrent live execution |
| `GET recovery/operations/{id}/events` | Immutable step events, cursor `after` and bounded `limit` |
| `GET recovery/operations/{id}/steps/{step_id}/retained` | Retained actual displaced preimage, while operation content is available |
| `GET/POST works/{id}/protection-links`, `POST .../unlink` | Explicit scope/session/batch association and removal |
| `GET works/{id}/protection-changes` | Shared directory observations and explicit task/batch associations |

Registration is idempotent and records the scope before its initial baseline attempt. Initial capture failure leaves an explicit incomplete scope, not a fabricated baseline. Version/capture reads are bounded; source connection and recovery writes are queued and survive HTTP disconnects. Refresh an operation by its returned ID; retry the same command with the same request ID. Reusing a request ID with different arguments is a conflict.

Version and batch summaries retain coverage and counts without loading their full JSON details. `details_included` says whether this response expanded files or entries; `metadata_available` says whether retained details can still be fetched. For a version, `history_status` distinguishes `FULL`, `CONTENT_EXPIRED` and `DETAILS_EXPIRED`. An expired-detail file-history row reports `UNKNOWN_DETAILS_EXPIRED`, never a proven absence. Single-file history extracts only the requested file from each paged version. Operation summaries retain `step_count` but leave `steps` and `evidence_refs` empty; use the full view or operation ID for those details.

## Capture, coverage and retention

The collector checks metadata every 2 seconds, waits for 2 seconds of stable observations and fully validates content every 30 seconds. Scan time adds to these intervals. It cannot capture every transient write or writes made while Core is stopped. Versions expose sampling start/end; they do not claim directory-wide instantaneous snapshots. Restart revalidates physical identity and expires old unused previews.

Complete scans record additions, changes, deletions and supported permission changes; rename is delete plus add. An incomplete scan, unreachable file, offline source or exhausted scan limit preserves prior history and never infers deletion. Bounded reads compare file identity and metadata around reads. Blob reads and dedup hits verify content hashes; GC respects immutable version, preview, operation and legacy checkpoint references.

Coverage uses `restorable`, `audit_only`, `excluded`, `unreachable` plus reason codes. Limits: 4 MiB per file, 128 MiB content per scan, 10,000 entries and depth 64. Sensitive content is audit-only, without saved plaintext. Links, special files, mount crossings, generated/dependency directories and unsupported metadata do not become recovery authority. Windows restoration uses current-user DACL and supported attributes. POSIX restoration requires current uid/gid, ordinary permission bits and no unsupported xattrs/ACLs. Existing parent directories are required; directory-tree reconstruction is not supported.

Default retention is 30 days / 2 GiB per scope. The scope quota uses `space.logical_retained_bytes`: retained compressed content, reserved or measured recovery storage, and logical metadata bytes. Transactional counters include UTF-8 metadata in versions, batches, previews, operations, journals, references, links, evidence and scope-owned management request receipts, with a small fixed allowance per row. Older management receipts without provable scope ownership remain part of shared physical storage; migration does not guess an owner. Metadata growth alone can exhaust quota and pause new capture. `shared_storage` reports database, WAL and free-page physical bytes separately; those shared physical totals are not the per-scope quota. A persisted five-minute collection interval avoids rescanning retention history on every unchanged capture or after service reopen; quota pressure still triggers collection immediately.

Either retention limit can release oldest non-pinned versions; baseline, current head, pinned versions and unfinished recovery dependencies are protected. Quarantine and operation receipts count toward scope usage. Expired unprotected versions release content and compact file details while retaining digest, coverage/count summaries and Work links. Content released only under quota pressure can still have retained file details. Unreclaimable quota pauses new capture. Restore preflight checks quota and free space in both the source filesystem and Core storage; external writers can still exhaust space after that check.

## Safe recovery

Modes are `undo_batch`, `restore_version`, `undo_operation`. Batch undo requires current selected files to match the batch end state. Historical restore previews compare current files with selected targets. Undo-operation uses the retained pre-operation states and a new preview.

A preview lasts five minutes and binds directory identity, connection epoch, selected file IDs, current contents and supported permissions. Selection changes require another preview. An active `undo_operation` preview retains its target bytes through collection without returning `content_b64` in the preview response. Execution obtains a global OS recovery lease, revalidates every selected file, verifies all required content/preimages and persists intent before each write. The adapter retains the actual displaced file using Windows replacement or Linux exchange/backup plus exclusive publication. Pinned directory handles keep path replacement from redirecting writes. Content and supported permissions are rechecked, and the resulting version must be recorded before success.

Step receipts preserve unexpected displaced versions as evidence. A failure compensates only if live content still matches this operation's write. External edits, uncertain publication, missing receipts or failed compensation produce `NEEDS_ATTENTION`; the operation and content remain pinned. Restart and response loss reconcile journal, adapter receipt, current file and isolation copies, without blindly replaying forward writes. If a tighter quota or an external writer makes safety evidence exceed the recovery reservation, the evidence remains durable and the scope pauses new capture with `over_quota_bytes` reported; repeated reconciliation without progress does not append duplicate evidence. No directory-wide atomicity is promised against arbitrary external writers. Unsupported safe filesystem operations fail closed.

The legacy HTTP `confirm: true`, CLI restore, transaction undo and HostWorkspace adapter live restore entry points now return `PREVIEW_REQUIRED`. Checkpoint reading, isolated test-restore and V3 import remain available.

## Environment and lifecycle

Native scopes bind volume/device plus directory file identity. Docker requires a local daemon, full container ID and revalidated mount/storage identity; bind mounts, named volumes and writable layers use the same fixed helper protocol. Remote daemons are unsupported. WSL enumeration includes stopped registered distributions, uses language-independent running-name queries and excludes Docker Desktop distributions. Only explicit start-and-connect may start a stopped distribution. Disconnect closes its channel; observation and protection never silently reopen it. Completed disconnect intent is retained across Core restart and applied before initial observation, including when Docker is temporarily offline. Docker protection requires a ping-verified container epoch; default read-only Agent discovery remains independent, and explicit disconnect pauses both process and session observation. Cross-environment protection connections must be explicitly re-established after Core restart; history remains readable while disconnected.

The helper runs fixed scan/recovery/process/session actions under existing Python 3.11 or newer. It does not execute caller shell code, install dependencies or elevate. Native sources cannot be disconnected; pause their scopes or observation policy explicitly. Explicit Docker and WSL connection requires a valid helper response; running state alone cannot make the connection operation succeed. Connection success does not imply directory accessibility or an established baseline.

Connected WSL process evidence includes boot/PID/start identity and independently observed cwd. Its session reader supports bounded Codex `session_meta` and installed Kimi Code `session_index.jsonl` metadata, without starting a provider broker. Provider-configured `CODEX_HOME` and `KIMI_CODE_HOME` are supported after absolute-path and non-symlink validation; callers cannot supply arbitrary store paths. Missing or unsafe stores return `UNKNOWN`; provider-store enumeration never proves process association. Docker Kimi and native Codex retain the existing provider adapters.

Core owns its worker lease, ordered shutdown and startup reconciliation. Desktop tray/window-close integration, new frontend flows, Agent execution/cancellation, multi-Agent orchestration, hunk selection, OS/package/service rollback and AI operations remain outside this backend delivery.

## Acceptance

See [the dated acceptance record](CORE_PROTECTION_ACCEPTANCE_20260929.md) for actual test results and unverified boundaries. The real environment suite is opt-in: `AGENTGUARD_LIVE_DOMAINS=1 python -m pytest tests/test_protection_live_api.py tests/test_protection_domains.py -q -s`. It uses disposable directories and dedicated Docker containers/volumes; it does not register real user projects or terminate the Ubuntu distribution.
