# Frontend / Backend Contract

Status: frozen backend wiring contract for K3 presentation integration

Backend baseline: `d22d2d80fdbccc64ac524ad40c14f74b0c7d7941` plus this closure

This document defines data and authority boundaries. It does not redesign the
K3 Desktop AppShell or Android Compose surfaces. K3 must preserve its existing
navigation, visual tokens, typography, spacing, state panels, localization, and
branding boundary.

## Transport and authority boundaries

- Core is the only product authority. It binds to `127.0.0.1:8787` and requires
  the process-local value from `GET /api/session` in `X-Session-Token` for every
  other `/api/*` request except health.
- Device Link is disabled by default. When explicitly enabled, it binds TLS 1.3
  to exactly one verified physical private-LAN IPv4 on port `8788`. It is not a
  proxy to Core.
- Every Core mutation target, execution domain, policy decision, checkpoint,
  action reference, and evidence relationship is server-owned.
- Device Link exposes bounded, sanitized projections and only two remote
  mutations: `APPROVE_ONCE` and `REJECT` for an existing Core supervision
  intent. It exposes no prepare/apply, checkpoint creation, test restore,
  restore, policy/baseline mutation, shell, file access, or generic Core route.
- AI is advisory. `POST /api/ai/analyze` accepts exactly `{}`; Core constructs
  sanitized context from its own projections. API keys are never part of DTOs,
  evidence, or Android state.
- `observed_at`, `updated_at`, and `analyzed_at` are displayable source times.
  The frontend must not invent stale thresholds or infer lifecycle transitions
  from elapsed time.

## Feature mapping

`—` means the feature is deliberately unavailable on that transport.

| Feature / K3 surface | Core endpoint and method | Device Link endpoint | Request DTO | Response DTO / principal fields | Authoritative and evidence source | States and common reason codes | Mutation / security notes |
|---|---|---|---|---|---|---|---|
| Home summary | Compose from the Core status, runtime, agents, supervision, changes, and recovery reads below | Compose from bounded status, environment, agents, supervision, changes, and recovery reads | none | Existing DTOs; preserve each child status independently | Core services and verified Evidence Ledger | `AVAILABLE`, `DEGRADED`, `EMPTY`, `UNKNOWN`, `UNREACHABLE`, `OFFLINE` where supplied | Never collapse degraded or unreachable data into healthy or empty |
| Environment status | `GET /api/status`, `GET /api/doctor`, `GET /api/v1/runtime`, `GET /api/v1/agents` | `GET /device/v1/environment`, `GET /device/v1/agents` | none | Runtime: `status`, `reason_code`, `observed_at`, `items[]`; agent: identity, role, lifecycle, confidence, domain, workspace, uncertainty, evidence refs | Discovery snapshots recorded in the existing Ledger | `R4_RUNTIME_AVAILABLE`, `R4_RUNTIME_DEGRADED`, `R4_AGENTS_AVAILABLE`, `R4_AGENT_DISCOVERY_UNREACHABLE`, `R4_WORKSPACE_BINDING_INCOMPLETE`, `R4_STATE_EMPTY` | Self runtime belongs only in runtime; do not synthesize it as an external agent |
| Discovery refresh | `POST /api/v1/discovery/refresh` | — | exactly `{}` | `product-discovery-1`: `status`, `reason_code`, `snapshot_id`, `observed_at`, `affected_views`, counts, `evidence_refs` | `ProductDiscoveryService` -> `record_discovery_snapshot` -> Ledger | `DISCOVERY_REFRESHED`, `DISCOVERY_REFRESH_UNAVAILABLE`, `DISCOVERY_REFRESH_REQUEST_INVALID` | After success refetch runtime, agents, supervision, recovery, and changes; no caller authority fields |
| Changes | `GET /api/v1/changes` | `GET /device/v1/changes` | none | `r4-p8-1`, view `changes`; bounded verified activities with event/time/result/change/checkpoint/session/domain/evidence summaries | One verified Ledger chain | `R4_CHANGES_AVAILABLE`, `R4_STATE_EMPTY`, `R4_LEDGER_INVALID`, `R4_DATABASE_UNREACHABLE` | No raw payload projection |
| Evidence detail | `GET /api/v1/evidence/{event_id}` | `GET /device/v1/evidence/{event_id}` | safe opaque event ID in path | `r4-product-evidence-1`: event metadata, chain ref, related refs, verification summary, bounded `sanitized_detail` | Exact event from verified Ledger chain | `AVAILABLE`, `DEGRADED`, `NOT_FOUND`; `EVIDENCE_EVENT_NOT_FOUND`, `EVIDENCE_EVENT_INVALID` | No arbitrary query and no raw private payload |
| Supervision | `GET /api/v1/supervision` | `GET /device/v1/supervision` | none | sessions with policy/status, approval/checkpoint requirements, opaque `action_ref`, timestamps, recent verified activities and evidence refs | `SupervisionService` plus verified Ledger events | existing session statuses; `R4_SUPERVISION_AVAILABLE`, `R4_STATE_EMPTY`, blocked/failed reason is explicit | UI presents backend status and never creates a second state machine |
| Approve once | `POST /api/v1/supervision/{session_id}/approve-once` | same suffix under `/device/v1` | exactly `{ "action_ref": "<64 lowercase hex>" }` | `r4-p8-action-1` / canonical service result | Existing `SupervisionService` and Ledger | canonical supervision reason codes; invalid request, stale/cross-session/replay, unavailable authority remain failures | One-shot, server-issued intent binding only; Android is allowed this mutation |
| Reject | `POST /api/v1/supervision/{session_id}/reject` | same suffix under `/device/v1` | exactly `{ "action_ref": "<64 lowercase hex>" }` | canonical supervision action result | Existing `SupervisionService` and Ledger | canonical rejection/action reason codes | One-shot; Android is allowed this mutation |
| Controlled change prepare | `POST /api/v1/supervision/changes` | — | exactly `{ "content": "<TOML>" }` | session ID, `action_ref`, decision, approval/checkpoint requirements, advisory, status, reason code | Existing controlled-change composition, policy, supervision, discovery, Ledger | canonical `CONTROLLED_CHANGE_*` codes | Server owns the sole target `config/agentguard.toml`; no caller path/domain/policy |
| Controlled change apply | `POST /api/v1/supervision/{session_id}/apply` | — | the exact original `{ "content": "<TOML>" }` | changed/verification/rollback/digests/checkpoint/evidence/status | Existing transaction, snapshot, supervision, and Ledger services | intent mismatch, stale, replay, approval, checkpoint, verification codes remain visible | Desktop only; refetch all product projections after success |
| Recovery summary and proof | `GET /api/v1/recovery` | `GET /device/v1/recovery` and alias `GET /device/v1/checkpoints` | none | checkpoint count/list/latest; manifest integrity; R1/R2/R3 facts; test/actual restore status; trusted baseline; capabilities; limitations; evidence refs | Existing `SnapshotStore`, `RecoveryCoverageService`, restore evidence, verified Ledger | `R4_RECOVERY_AVAILABLE`, `R4_STATE_EMPTY`, `EVIDENCE_INSUFFICIENT`; actual restore is `VERIFIED` only after real verified restore evidence | Checkpoint existence alone is not recovery proof |
| Create checkpoint | `POST /api/v1/recovery/checkpoints` | — | exactly `{}` | `product-recovery-action-1`, checkpoint ID, manifest digest, evidence refs | Existing `RecoveryService` over server-owned product config target | `RECOVERY_SNAPSHOT_CREATED` or explicit failure | Desktop only; target and domain are server-owned |
| Test restore | `POST /api/v1/recovery/{checkpoint_id}/test` | — | exactly `{}` | canonical recovery action fields and verified target count | Existing `RecoveryService.test_restore` | `TEST_RESTORE_VERIFIED` or explicit failure | Non-mutating verification of checkpoint scope; Desktop only |
| Restore | `POST /api/v1/recovery/{checkpoint_id}/restore` | — | exactly `{ "confirm": true }` | canonical action fields and evidence refs | Existing `RecoveryService.restore`, manifest validation, local runtime adapter, Ledger | `RECOVERY_RESTORED_AND_VERIFIED`, `RECOVERY_CONFIRMATION_REQUIRED`, or explicit failure | Explicit confirmation; server-owned target; Desktop only |
| AI provider settings/test | existing `POST /api/ai/models` and `POST /api/ai/test` | — | existing provider configuration DTOs | sanitized model/test results | Existing `OpenAICompatibleProvider` in process memory | existing provider reason/error contract | Never persist or echo the API key |
| Analyze current environment | `POST /api/ai/analyze` | — | exactly `{}` | `product-ai-advisory-1`: severity, summary, uncertainties, checks, evidence refs, provider/model/time | Core-built runtime, agents, supervision, changes, and recovery context; provider is advisory only | `AI_ADVISORY_AVAILABLE`, `AI_PROVIDER_UNAVAILABLE`, `AI_ANALYZE_REQUEST_INVALID` | Caller cannot inject facts or authority |
| AI advisory | Result of Core analyze | `GET /device/v1/ai/advisory` | none | latest sanitized advisory or explicit unavailable DTO | Latest in-process Core advisory | `AVAILABLE` / `UNAVAILABLE`; `AI_ADVISORY_AVAILABLE`, `AI_ADVISORY_NOT_RUN` | No secret/provider configuration on Android |
| Devices / link state | `GET /api/v1/devices` | `GET /device/v1/status` after authentication | none | lifecycle status, exact endpoint/address/subnet, durable Desktop UUID/fingerprints, bounded devices/session counts; Device status includes permissions | `DeviceLinkController`, durable identity, SQLite binding registry | `ENABLED`, `DISABLED`, `DEGRADED`; `DEVICE_LINK_ENABLED`, `DEVICE_LINK_DISABLED`, `DEVICE_LINK_AUTHORITY_UNAVAILABLE` | Core route is loopback-only; 8788 status requires a live product token |
| Enable / disable | `POST /api/v1/device-link/enable`, `/disable`, `/network/refresh` | — | exactly `{}` | `device-link-lifecycle-1` | Physical adapter/route/profile observation, owned listener/firewall state | LAN unavailable/ambiguous, observation/firewall/listener/rebind failures are explicit | Disabled by default; disable removes exposure but preserves binding; no host/IP input |
| Pairing invitation | `POST /api/v1/device-link/pairings`; status/desktop confirm/cancel under returned session ID | pairing connect/SAS/Android confirm/complete under `/device/v1/pair/{session_id}` | Core actions use `{}` or `{ "confirm": bool }`; 8788 DTOs are strict and pairing-token scoped after connect | invitation fields listed below; SAS state; completion returns short-lived product token and permissions | Existing P-256/SAS state machine plus one-time memory ticket and durable binding store | created, first connection, SAS pending, confirmed both, consumed/rejected/cancelled/expired; explicit `PAIR_*` codes | Both devices explicitly confirm the same SAS; ticket and pairing tokens are memory-only/digest-keyed |
| Auth / re-auth | — | `POST /device/v1/auth/challenge`, then `/auth/response` | bound Android UUID + protocol v1; response includes challenge ID and DER ECDSA signature | one-use challenge; short-lived session token; Desktop signature over the same domain-separated message | Durable Desktop and Android P-256 identities plus durable public binding | bound/not-bound, unknown/used/expired/mismatched challenge, invalid signature | Android verifies Desktop signature; session expiry triggers re-auth, not re-pairing |
| Reconnect after IP change | Core `POST /api/v1/device-link/network/refresh` rebinds exposure | saved-UUID UDP rediscovery on exact address/port, then normal auth/read routes | discovery request names exact saved Desktop UUID | candidate endpoint contains exact UUID/port/TLS fingerprint | Existing durable binding and fingerprints; rediscovery response itself is not trust | connected/offline/last-known in repository | No generic LAN browsing, manual-IP onboarding, or silent new trust |
| Revoke / unpair | `POST /api/v1/device-link/devices/{device_uuid}/revoke` | Android local repository `unpair()` | Core exactly `{}`; Android local action | revoked/removed state | Durable binding store and in-memory token invalidation | `revoked`, `DEVICE_NOT_FOUND` | Desktop revoke prevents old credentials reconnecting; Android unpair removes binding and KeyStore identity; both require new QR+SAS next time |

## Pairing QR encoding

Core returns a structured invitation. K3 may render it as a QR but must not
invent fields or use the endpoint as a trust root. The canonical URI is:

```text
agentstate://pair?v=1&host=<private-ip>&port=8788&uuid=<desktop-uuid>&pub=<desktop-public-der-hex>&sign_fp=<sha256-spki>&tls_fp=<sha256-spki>&sid=<32-hex>&ticket=<64-hex>&exp=<unix-epoch-seconds>
```

Field mapping from `POST /api/v1/device-link/pairings`:

| QR field | Invitation field |
|---|---|
| `v` | `protocol_version` |
| `host`, `port` | parsed from `endpoint`; port must be 8788 |
| `uuid` | `desktop_uuid` |
| `pub` | `desktop_public_key_der` |
| `sign_fp` | `desktop_signing_fingerprint` |
| `tls_fp` | `tls_spki_fingerprint` |
| `sid` | `session_id` |
| `ticket` | `ticket` |
| `exp` | `expires_at_epoch` |

## Android repository contract

- `DeviceLinkClient` consumes only an explicit QR endpoint or a durable
  last-known binding. Production has no emulator default or cleartext path.
- `DeviceIdentity` creates a non-exportable P-256 signing key in
  `AndroidKeyStore`. `DeviceBindingStore` persists only safe binding metadata;
  session and pairing tokens remain memory-only.
- `DeviceLinkRepository` is the future K3 ViewModel boundary. It provides
  explicit/foreground refresh, last-known data with an offline transport state,
  session-expiry re-authentication, exact-bound-UUID rediscovery, endpoint
  update after identity/TLS/auth checks, approve/reject, and unpair.
- Cleartext localhost/emulator exceptions exist only in the debug resource
  overlay. The production manifest config is cleartext-disabled and the
  production transport itself is HTTPS-only.

## K3 wiring checklist

- Preserve every backend status/reason code and show source timestamps; do not
  synthesize health, freshness, runtime lifecycle, recovery proof, or pairing
  success.
- Desktop: wire Home, Environment, Changes, Supervision, Recovery, Devices,
  Settings/AI, evidence detail, and the controlled-change flow to the Core rows
  above.
- Android: wire the existing Compose presentation to `DeviceLinkRepository`,
  QR scanning/confirmation, bounded projections, approve/reject, reconnect,
  offline last-known state, and unpair. Do not let Screens own HTTP clients.
- No package icon may be treated as final branding until the agreed canonical
  source asset is recovered or confirmed.

## Deliberately absent

There is no public Internet/cloud transport, relay, generic LAN browser,
manual-IP onboarding, multi-device framework, realtime event protocol,
background daemon, replicated Android database, auto-approval, auto-restore,
or Device Link access to privileged Core mutation surfaces.
