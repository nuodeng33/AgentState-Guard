# Current State

**Version:** 0.9.0.dev0

**Branch:** `feat/device-link`

**P0-A start:** `1ae3ad8086ae9c3e2713190b16d013d622037169`

**Phase contract:** `a0a13e9`

**P0-A implementation:** `7c4277d`, `20331bb`

**Security status:** **PARTIAL**

## Device Link P0-A

Implemented and locally verified:

- DER SubjectPublicKeyInfo P-256-only signature verification.
- Monotonic terminal pairing state and atomic five-session capacity.
- Pair-completion identity binding with no mismatch side effects.
- Digest-only, expiring, replacing, revocable in-memory session tokens.
- Server-issued, device-bound, expiring, attempt-bounded, one-time challenges.
- Domain-separated, length-prefixed authentication message.
- Real FastAPI statuses and strict request models.
- Loopback peer/startup gate plus remote browser-origin rejection.
- Streaming 1 MiB request-body cap.
- Minimal concurrency correctness for capacity, completion, and replay.
- Production FastAPI security tests; fake HTTP security routers removed.

Not implemented or not verified:

- Independent cross-participant SAS and double confirmation.
- TLS, mDNS, certificate pinning, or secure LAN exposure.
- Android cryptography/Keystore runtime closure.
- Persistent device/token/challenge/revocation state.
- Physical Android-to-Windows end-to-end validation.

## Verification

| Check | Result |
| --- | --- |
| Starting targeted Device Link baseline | 88 tests: 86 passed, 2 failed |
| P0-A targeted suite | 107 passed, 0 failed, 0 skipped |
| Starting full Python baseline on Windows | 227 tests: 207 passed, 20 failed |
| Full Python after implementation | 246 tests: 229 passed, 17 failed |
| Failure-set comparison | 3 baseline failures resolved, 0 new failures |
| Ruff on changed Device Link/server/tests | passed |
| GitHub combined status at starting commit | no status contexts returned |
| Android tests | blocked: no Gradle launcher/wrapper JAR; Java 8 only |

The remaining 17 failures are pre-existing Windows/POSIX assumptions:
`os.fchmod`, Unix command names, `/tmp`, and POSIX path/whitelist semantics.
They are outside P0-A and were not hidden or reclassified as pass.

## Runtime boundary

The current gateway is local-only HTTP. Older documents describing LAN
HTTPS/WSS, Ed25519, mDNS, pinning, or Keystore are future design targets unless
explicitly marked otherwise. The executable contracts are
`docs/device-link/API_CONTRACT.md` and `docs/device-link/PROTOCOL.md`.

## Next permitted work

P0-A containment is complete. Any P0-B, TLS/SAS/Android compatibility work, or
remote transport work requires a new phase contract.

The Android client is not compatible with the P0-A runtime contract: complete
omits `protocol_version`, tests use a 16-byte nonce and a non-DER dummy key,
the emulator endpoint cannot reach a loopback-only gateway, and no
challenge/response client flow exists.
