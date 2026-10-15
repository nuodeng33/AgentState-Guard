# R4-P8 UI Backend Contract

## Requirement IDs

- R4-P8-001 Runtime authoritative read DTO
- R4-P8-002 Agents authoritative read DTO
- R4-P8-003 Supervision authoritative read DTO
- R4-P8-004 Recovery authoritative read DTO
- R4-P8-005 No caller-supplied authority injection
- R4-P8-006 Stable empty / unknown / degraded semantics
- R4-P8-007 No secret, path, raw DB, or raw exception leakage

## Scope

Freeze the minimal read-only backend contract consumed by later UI work. This phase adds only authoritative `/api/v1/runtime`, `/api/v1/agents`, `/api/v1/supervision`, and `/api/v1/recovery` routes with deterministic empty-state responses and token-protected access.

## Non-Goals

- No React/UI work
- No Android UI changes
- No mutation endpoints
- No caller-provided recovery/policy/agent truth
- No broad refactor of legacy `/api/*` routes
- No discovery collection orchestration or background polling

## Authoritative Sources

- Runtime: allowlisted fields from verified `RUNTIME_DETECTED` and `PROBE_UNREACHABLE` Ledger events
- Agents: allowlisted fields from verified `AGENT_DETECTED` Ledger events; unknown workspace binding remains explicit
- Supervision: durable `supervision_sessions` plus ledger-backed policy events
- Recovery: `RecoveryCoverageService.compute(...).safe_summary()` and trusted baseline lifecycle records

## DTO Contract v1

All `/api/v1/*` routes return JSON with:

- `schema_version`: `r4-p8-1`
- `view`: one of `runtime`, `agents`, `supervision`, `recovery`
- `status`: `EMPTY | UNKNOWN | DEGRADED | AVAILABLE`
- `reason_code`: stable machine-readable string
- `evidence_refs`: array of stable evidence references
- `items`: array for view-specific collections

`/api/v1/recovery` additionally returns:

- `recovery_level`: `R0 | R1 | R2 | R3`
- `r1_verified`: boolean
- `r2_verified`: boolean
- `r3_verified`: boolean
- `test_restore_status`: stable service value
- `trusted_baseline_status`: `NONE | TRUSTED | RETIRED`
- `trusted_baseline_id`: string or `null`

## Empty-State Semantics

- Empty database or no authoritative records returns `status: EMPTY`
- `reason_code` is `R4_STATE_EMPTY`
- `items` is `[]`
- Recovery empty state returns `R0`, `r3_verified: false`, `trusted_baseline_status: NONE`
- Database or verified-Ledger failure returns `DEGRADED` with a stable reason code and no projected authority
- Missing or corrupt Snapshot V3 content forces the affected recovery item to `R0`
- Runtime and agent payloads use explicit field allowlists; arbitrary Ledger payload keys are never reflected

## Security Rules

- These routes remain behind `X-Session-Token`
- Query parameters and caller input never override authoritative truth
- Responses must not include raw local paths, tokens, passwords, command lines, environment, raw DB rows, or exception text

## Affected Files

- `agentguard/api/server.py`
- `agentguard/api/r4_projection.py`
- `tests/test_api_r4_contract.py`
- `docs/phases/08-ui-backend-contract.md`
- phase completion docs after verification

## Risks

- Later UI may assume richer fields than this frozen minimal contract
- Legacy `/api/*` and new `/api/v1/*` can diverge if future changes are not documented

## Tests

- `tests/test_api_r4_contract.py`
- full pytest regression after implementation
- compileall, scoped Ruff, diff-check, wheel build/install, CLI smoke at release gate

## Rollback Point

Revert only the P8 contract commit(s) touching the API contract and docs; do not reset or clean the branch.

## Exit Criteria

- RED contract tests exist and fail before production implementation
- populated discovery, supervision, and recovery state is projected from verified server-owned sources
- `/api/v1/runtime`, `/api/v1/agents`, `/api/v1/supervision`, `/api/v1/recovery` return deterministic empty-state DTOs
- session token is required
- caller-provided authority query parameters do not change authoritative fields
- database, Ledger, and Snapshot faults fail closed without raw exceptions
- no path/secret leakage in test responses
