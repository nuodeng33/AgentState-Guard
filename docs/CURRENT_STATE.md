# Current State

**Version:** 0.9.0.dev0
**Branch:** feat/device-link
**HEAD:** local R4-P7 recovery trust boundary series (verify with `git rev-parse HEAD`)

## V1 Passive Agent Discovery Closeout — 2026-08-22

V1 passive discovery only admits bounded known identity evidence. Arbitrary
unknown harness discovery from generic process metadata is deferred because
current observable facts cannot distinguish it reliably from ordinary tooling.
Future explicit authoritative provenance may enable managed unknown targets,
but is not a V1 requirement.

- Codex, Claude, and Kimi bounded passive detection remains required.
- Generic process/tooling metadata, Cursor, and OpenCode do not receive passive
  admission without bounded identity evidence.
- Workspace binding and mutation permission remain independent and fail closed.

## R4-P7 Recovery Trust Boundary

- Schema v7 stores recovery-drill and trusted-baseline candidate bindings to durable Supervision sessions plus expiry/nonce/consume-bound recovery authorizations.
- `VERIFIED_R3` remains distinct from `TRUSTED`; baseline confirmation requires a separate approved Supervision authorization and one SQLite transaction for consume, state transition, immutable baseline write, and Evidence Ledger append.
- Server-computed coverage exposes R0/R1/R2/R3 and baseline lifecycle facts only from StateDB, Snapshot V3, durable recovery records, and a verified ledger. BLOCK/UNKNOWN, scope drift, stale/mismatched evidence, replay, and retirement fail closed.
- Recovery CLI provides inspection and baseline create/approve/confirm/retire/show commands and does not accept caller-supplied recovery truth.
- Fresh local evidence: 753 Python tests passed; changed-file Ruff passed; wheel built and installed into a clean non-editable venv; Recovery CLI help and read-only show smoke passed. Push and CI audit remain release gates until recorded.


- Local commits establish transaction foundation, schema v3 Evidence Ledger, schema v4 supervision sessions, deterministic policy, project-local Retry Guard evidence, local supervision CLI, and an offline closure regression.
- Fresh pre-document targeted evidence: Retry Guard 9 passed; Supervision/migration/policy/evidence/CLI related regression 35 passed; offline closure 3 passed. Final full-suite and CI evidence must be collected separately.
- Safety boundary: BLOCK and UNKNOWN do not activate, REVIEW requires explicit approval, checkpoint requirements remain active, and session plus ledger writes share one `BEGIN IMMEDIATE` transaction.
- See `docs/R4_P4_SUPERVISION.md` for contracts and explicit non-goals.

## Core CI Status

| Check | Result |
|-------|--------|
| Full pytest (local) | **227 passed, 0 failed, 0 skipped** |
| commit | e6267ea |
| GitHub Core CI | PENDING (pushed, awaiting runner) |

## Changes in e6267ea (committed)

All 9 previous Core CI failures resolved:

| # | Test | Type | Fix |
|---|------|------|-----|
| 1 | test_replay_cache_evicts_properly | B | size() counts live entries only |
| 2 | test_terminal_reject_all_events | A | EXPIRED + FAILED paths trigger state transition |
| 3 | test_full_pairing_flow | B | Added gw.get_status() + /device/v1/status route |
| 4 | test_unauthenticated_read_rejected | B | Same as #3 |
| 5 | test_oversized_payload_rejected | C | 1MB Content-Length cap in HTTP handler |
| 6 | test_replay_mutation_killed | A | Added ReplayCache import |
| 7 | test_expiry_mutation_killed | A | setup() → setup_method() |
| 8 | test_transcript_field_mutation_killed | A | Use TRANSCRIPT_SECRET (separated const) |
| 9 | test_terminal_reactivation_mutation_killed | A | Same as #7 |

## Android Runtime Closure (pending commit)

| Change | Status |
|--------|--------|
| DeviceLinkClientTest.kt (JVM, 7 tests) | WRITTEN |
| DeviceLinkClientInstrumentedTest.kt (emulator) | WRITTEN |
| build.gradle.kts: androidTest deps | WRITTEN |
| server.py: nonce_hex → nonce (field name mismatch) | WRITTEN |
| emulator.yml: boot progress + logcat streaming | WRITTEN |
| emulator.yml: DeviceLinkClient instrumented test step | WRITTEN |

## Device Link MVP (14 requirements)

| ID | Description | Status |
|----|-------------|--------|
| DL-MVP-001..016 | 14 read-only MVP reqs | DESCRIBED in MVP_SPEC |

## Source Lines
| Module | Lines |
|--------|-------|
| crypto.py | 230 |
| pairing.py | 195 |
| gateway.py | 224 |
| api/server.py | 285 |
| **Total** | **934** |

## Test Assertions
| File | Count |
|------|-------|
| test_device_link_crypto.py | 19 |
| test_device_link_pairing.py | 17 |
| test_device_link_audit.py | 20+ |
| test_device_link_integration.py | 7 (HTTP) |
| test_device_link_e2e.py | 28+ |
| DeviceLinkClientTest.kt (JVM) | 7 |
| DeviceLinkClientInstrumentedTest.kt | 1 |
| **Total** | **99+** |

## Next Phase: Phase 1 (Tauri scaffold) + Runtime Closure

## Device Link Stable Development Baseline — 2026-07-27

- Independent adversarial review rejected the first audit patch because FastAPI treated `Request` as a query parameter, pairing expiry could be overwritten, `pair_ttl` was not propagated, expired sessions occupied capacity, and the Android HTTPS-only client could not connect to the HTTP development server.
- Closed the immediate regressions: production Bearer routes now return 401/200 rather than 422; pairing terminal states are absorbing and clear secrets; configured pair TTL and expired-session capacity cleanup are enforced; `pair_complete` consumes before binding so expiry leaves no registry side effect; Android development transport is interoperable over HTTP while retaining `Authorization: Bearer`.
- Fresh local evidence: **281 Python tests passed**; all seven Device Link Python files passed (**117 tests**); Android JVM JUnit XML reports **11 passed, 0 failed, 0 skipped**; `compileall` and `git diff --check` passed.
- Stable development baseline: **YES**. Merge-ready: **NO** because TLS 1.3/server certificate handling/SPKI pinning, real dual SAS confirmation, complete mutual challenge transcript, UUID canonicalization, and full ASDL/1 route/schema convergence remain open.
