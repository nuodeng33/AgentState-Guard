---
name: contract-drift-debugger
description: Diagnose API contract drift, Python/Kotlin mismatch, golden vector divergence, schema mismatch, and protocol migration failures across implementation boundaries.
---

# Contract Drift Debugger

## Trigger Scope

Invoke for:
- API drift / HTTP contract mismatch
- Test assertion mismatch against spec
- Python vs Kotlin implementation divergence
- Schema / constructor / method signature mismatch
- Golden vector divergence (reference vs production)
- Protocol migration inconsistencies (e.g., field name, byte order, encoding)

## Four-Way Comparison

Before diagnosing any drift, establish ALL four reference points:

1. **Current specification** — `docs/device-link/PROTOCOL.md`, `API_CONTRACT.md`, `MASTER_SPEC.md`
2. **Current production implementation** — `crypto.py`, `pairing.py`, `gateway.py`, `server.py`
3. **Existing tests** — `test_device_link_*.py`, reference_crypto.py
4. **Consumer implementation** — Kotlin `DeviceLinkClient.kt`, `QrPayload.kt`

## Drift Classification

Every drift MUST be classified into exactly one category:

| Code | Category | Action |
|------|----------|--------|
| **A** | Test obsolete, implementation follows current spec | Update test |
| **B** | Implementation violates current spec | Fix implementation |
| **C** | Spec ambiguous | Document ambiguity, request spec clarification before fixing |
| **D** | Consumer drift (Kotlin diverged from Python) | Fix consumer to match source of truth (spec or reference) |
| **E** | Reference/golden vector drift | Fix reference when it lags; fix production when it diverges |

**NEVER** change an assertion just because a test is red.
**NEVER** assume the existing implementation is the source of truth.

If spec conflicts with implementation: **document the conflict in the diagnosis ledger** and request a decision.

## Golden Vector Debugging — Cross-Language

When Python and Kotlin produce different results for the same input:

### Step 1: Find the first divergent byte

Compare byte-by-byte:
- Canonical payload bytes (Python `build_pairing_transcript` vs Kotlin equivalent)
- Transcript hash (SHA-256 of canonical bytes)
- Fingerprint (SPKI DER → SHA-256 → hex)
- SAS (HMAC output bytes → first 3 bytes)

### Step 2: Locate the earliest difference

For each field in the canonical payload:
1. Serialize the field alone in both languages
2. Compare byte-by-byte
3. The first field where bytes differ is the drift root

### Step 3: Determine cause

- Different field order? → Field ordering spec violation
- Different encoding? (e.g., UTF-8 vs UTF-16, big-endian vs little-endian)
- Missing field? → One side doesn't include a field
- Extra field? → One side includes more data

### Golden Vector Rule

The reference implementation (`reference_crypto.py`) is the independent truth oracle.
It must NOT import the production implementation (`agentguard`). Verify this first.

## Protocol Migration

When field names change (e.g., `nonce_hex` → `nonce`):
1. Check ALL consumers: Python Gateway, Kotlin client, tests
2. A mismatch means at least one side is stale
3. Fix the consumer, not the spec
