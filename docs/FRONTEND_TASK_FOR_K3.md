# FRONTEND_TASK_FOR_K3

## Scope and frozen presentation boundary

- Start from `feat/product-ui-i18n-k3` at validated commit
  `aae79004814ae7ead007680f7995706a1fbca915`.
- Preserve the existing K3 Desktop AppShell, navigation, state panels, visual
  tokens, typography, spacing, cards, localization, and Android Compose shell.
- This task is wiring and truthful state presentation only. Do not import K3
  network or authority semantics into the R4 backend.
- The backend owns target selection, target path, execution domain, policy,
  checkpoint, evidence, and supervision authority. The UI must never send
  fields for any of them.

## Desktop API contract

All API calls use the existing loopback origin and the session token returned
by `GET /api/session` in `X-Session-Token`.

### Discovery and state

- `POST /api/v1/discovery/refresh` with exactly `{}`. Extra fields are rejected
  with HTTP 422 and `reason_code=DISCOVERY_REFRESH_REQUEST_INVALID`.
- A successful refresh returns:
  `schema_version`, `status`, `reason_code`, `snapshot_id`, `runtime_count`, and
  `agent_count`.
- `reason_code=DISCOVERY_REFRESH_UNAVAILABLE` returns HTTP 503. Preserve the
  returned `DEGRADED`/`UNREACHABLE` state; do not render it as empty or healthy.
- After refresh, refetch `GET /api/v1/runtime`, `GET /api/v1/agents`,
  `GET /api/v1/supervision`, and `GET /api/v1/recovery`.
- The self runtime appears only in runtime state. External processes appear as
  agents. Do not synthesize an AgentState Guard agent card from the self
  runtime.

### Controlled configuration change

The only product-controlled mutation target is the server-owned
`config/agentguard.toml`.

1. `POST /api/v1/supervision/changes` with `{ "content": "<toml>" }`.
2. Store the returned `supervision_session_id` and `action_ref`. Present the
   returned `decision`, `requires_manual_approval`, `requires_checkpoint`,
   `ai_advisory`, `status`, and `reason_code` without reinterpretation.
3. Manual approval:
   `POST /api/v1/supervision/{session_id}/approve-once` with
   `{ "action_ref": "<64 lowercase hex>" }`.
   Manual rejection uses the same body at
   `POST /api/v1/supervision/{session_id}/reject`.
4. Only after an approved response, call
   `POST /api/v1/supervision/{session_id}/apply` with the exact same
   `{ "content": "<toml>" }` used at prepare time.
5. Render `changed`, `verification`, `rolled_back`, `before_digest`,
   `after_digest`, `checkpoint_id`, `evidence_refs`, `status`, and
   `reason_code`. Then refetch the four state endpoints above.

Do not expose controls for caller-selected path, target, domain, policy,
checkpoint, evidence reference, or authority binding. A changed content body
between prepare and apply must remain a visible backend rejection, not an
automatic retry.

## Android honesty boundary

Until a production device transport and pairing backend are implemented, keep
QR scan, LAN discovery, manual address entry, pairing, and remote mutations
disabled or explicitly unavailable. Do not label demo/local data as connected,
paired, synchronized, or live.

## Branding boundary

The K3 `ShieldNodesMark` presentation is the persisted product mark currently
visible in source. Existing Tauri PNG/ICO files and Android launcher defaults
remain packaging placeholders. K3 must obtain or confirm the agreed canonical
source asset before generating Windows ICO sizes or Android adaptive launcher
resources; do not generate a replacement logo.

## Required K3 verification

- Desktop state panels preserve backend `AVAILABLE`, `DEGRADED`, `OFFLINE`,
  `UNKNOWN`, and `UNREACHABLE` distinctions.
- Refresh rejects injected authority fields and updates all state panels.
- Fresh install completes prepare, approve, apply, verification, diff, and
  evidence display without any caller-selected target.
- Reject and intent-mismatch paths remain visible and do not mutate the file.
- Android presents no false device-link success state.
- No packaging asset is accepted as final branding until its canonical source
  is verified.
