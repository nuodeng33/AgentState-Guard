# R4-P8 Supervision Action Contract

## Scope

This contract freezes the backend mutation boundary used by later K3 frontend wiring. It does not modify deterministic policy, create checkpoints, activate operations, or authorize recovery-specific operations.

Schema version: `r4-p8-action-1`.

## Authentication

Both routes require the in-memory `X-Session-Token` issued by `GET /api/session`. Missing or invalid authentication returns HTTP 401.

Browser origins are restricted to the packaged Tauri origins and the fixed local development origins. The public session bootstrap is not readable by arbitrary web origins.

## Read Binding

`GET /api/v1/supervision` adds `action_ref` to each item:

- a 64-character opaque server binding only for a verified, non-recovery `REVIEW` session in `AWAITING_APPROVAL`;
- `null` for `ALLOW`, `BLOCK`, `UNKNOWN`, terminal, invalid-Ledger, legacy-unbound, and recovery-specific sessions.

The binding covers the session identity, immutable policy authority, checkpoint requirement, authoritative policy Evidence, and the latest Evidence event for that session. Any same-session Evidence or state drift invalidates an older binding.

## Approve Once

`POST /api/v1/supervision/{session_id}/approve-once`

Request JSON:

```json
{"action_ref":"<64 lowercase hex characters>"}
```

No other field is accepted. The action is valid only for one server-owned `REVIEW` session with `status=AWAITING_APPROVAL` and `requires_manual_approval=true`.

Success records exactly one `USER_APPROVED` event and changes only the session status to `APPROVED`. It does not clear `requires_checkpoint`, create a checkpoint, call `activate()`, change policy to `ALLOW`, or authorize a recovery drill.

## Reject

`POST /api/v1/supervision/{session_id}/reject`

The request schema is identical to Approve Once. Success records exactly one `USER_REJECTED` event and transitions `AWAITING_APPROVAL` to terminal `REJECTED`. The original policy decision remains `REVIEW`.

## Success Response

```json
{
  "schema_version": "r4-p8-action-1",
  "action": "APPROVE_ONCE",
  "supervision_session_id": "session-00000000-0000-0000-0000-000000000000",
  "status": "APPROVED",
  "reason_code": "SUPERVISION_APPROVED_ONCE",
  "consumed": true,
  "evidence_refs": ["session-event-00000000-0000-0000-0000-000000000000"]
}
```

Reject uses `action=REJECT`, `status=REJECTED`, and `reason_code=SUPERVISION_REJECTED`.

After either success, refetch `GET /api/v1/supervision`.

## Stable Failure Mapping

All action failures return the action schema with `status=UNCHANGED`, `consumed=false`, and an empty `evidence_refs` array.

| HTTP | reason_code | Meaning |
| --- | --- | --- |
| 401 | existing unauthorized response | Missing or invalid session token |
| 404 | `SUPERVISION_SESSION_NOT_FOUND` | No server-owned session exists |
| 409 | `SUPERVISION_POLICY_NOT_APPROVABLE` | Policy is not `REVIEW` or manual approval is not required |
| 409 | `SUPERVISION_ACTION_STALE` | The opaque binding does not match current authority |
| 409 | `SUPERVISION_ACTION_REPLAYED` | An action was already consumed or the session is terminal |
| 409 | `SUPERVISION_ACTION_INVALID_STATE` | The session is not awaiting approval |
| 409 | `SUPERVISION_RECOVERY_AUTHORIZATION_REQUIRED` | P7 recovery-specific authorization is required |
| 422 | `SUPERVISION_ACTION_REQUEST_INVALID` | Malformed ID/body, invalid binding format, or extra fields |
| 503 | `SUPERVISION_AUTHORITY_UNAVAILABLE` | Database, append, or commit failed |
| 503 | `SUPERVISION_LEDGER_INVALID` | The append-only hash chain did not verify |
| 503 | `SUPERVISION_POLICY_EVIDENCE_INVALID` | Required or unique policy Evidence could not be proven |

Raw exceptions, SQL, database paths, commands, secrets, tokens, and environment values are never returned.

## Replay, Concurrency, and Atomicity

The compare-and-update plus `USER_APPROVED` or `USER_REJECTED` append runs inside one `StateDB.transaction()` using `BEGIN IMMEDIATE`. Duplicate, reordered, cross-session, and concurrent requests produce exactly one legal state transition and one corresponding Evidence event. Append or commit failure rolls back both state and Evidence.

## Recovery Isolation

Sessions bound by `recovery_drill_bindings` or `trusted_baseline_candidate_bindings` never receive a generic `action_ref` and both generic routes reject them. P7 authorization IDs, nonces, expiry, checkpoint/domain/manifest binding, R3 verification, and Trusted Baseline authority remain unchanged.
