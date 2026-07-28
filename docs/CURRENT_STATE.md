# Current State

**Version:** 0.9.0.dev0
**Branch:** feat/device-link
**HEAD:** e6267ea (Core CI fix batch)

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
