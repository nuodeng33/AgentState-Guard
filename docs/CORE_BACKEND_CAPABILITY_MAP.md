# Core Backend Capability Map

Audit baseline: `d22d2d80fdbccc64ac524ad40c14f74b0c7d7941`

## Repository state

- Current branch: `fix/product-dogfood-unblock`
- Working tree at audit start: clean
- Required baseline `d22d2d80fdbccc64ac524ad40c14f74b0c7d7941`: present
- Requested object `1a057326988a3e5fbc2a5d1330f85e86fa736855`: absent
- Local `feat/product-ui-i18n-k3`: absent
- Remote `origin/feat/product-ui-i18n-k3`: present at
  `aae79004814ae7ead007680f7995706a1fbca915`
- Uncommitted K3 UI work in this worktree: none

## Closed production call graph

| Capability | Existing authority reused | Product path after closure | Closure result |
|---|---|---|---|
| Runtime discovery | `ProductDiscoveryService`, `SelfRuntimeAdapter` | Packaged startup and strict `{}` refresh record one canonical discovery snapshot and return time, affected views, counts, and evidence refs | Connected |
| Agent discovery | `ProcessCollector`, `PsutilProcessBackend` | Known external agents enter the existing Ledger; self runtime remains only in runtime | Connected with observation time and explicit uncertainty |
| Evidence | `EvidenceLedger`, `EvidenceEvent`, `verify_ledger` | All projections consume one verified chain; Core and 8788 expose bounded detail by exact event ID | Connected without raw payload exposure |
| Runtime/agents read model | `R4ReadProjectionService` | Existing projections are enriched in place; no replacement service exists | Connected |
| Supervision | `SupervisionService` | Core and Device Link share the same sessions, opaque action refs, one-shot approve/reject, and verified activity projection | Connected; no second authority |
| Controlled change | `prepare_controlled_change`, `apply_controlled_change` | Fresh-install server-owned `config/agentguard.toml` prepare/approve/apply/checkpoint/verify loop remains canonical | Preserved and regression-covered |
| Changes | Existing Ledger events | `GET /api/v1/changes` and bounded 8788 equivalent reuse one verified-activity helper | Connected |
| Snapshot/recovery | `SnapshotStore`, `RecoveryService`, `RecoveryCoverageService`, `SelfRuntimeAdapter` | Full summary plus server-owned checkpoint/test/confirmed real restore actions; actual restore proof derives only from verified restore evidence | Connected; no second recovery system |
| AI provider | `OpenAICompatibleProvider` | Provider test/models stay existing; analyze is intent-only and Core-built; latest sanitized advisory is projected to 8788 | Connected as advisory only |
| Device crypto/pairing | Existing P-256 helpers, SAS transcript, `PairingManager`, `PairingSession` | One-time QR ticket, pairing token, dual confirmation, domain-separated mutual auth, re-auth, and token invalidation extend the existing gateway | Productized without replacing primitives |
| Desktop identity | Existing P-256 serialization primitives | Stable UUID plus distinct signing/TLS keys in product state; current-user DPAPI on Windows and mode-0600 development fallback | Durable |
| Device binding | Core SQLite migration system | Minimal public binding metadata and revocation state in migration 8; live credentials remain memory-only | Durable and restart-safe |
| Device gateway | Existing gateway primitives | Independent allowlisted TLS 1.3 FastAPI/uvicorn app on 8788; Core 8787 no longer mounts `/device/v1` | Separated |
| Listener/network | Windows route/adapter/firewall system observations | Disabled-by-default controller selects one private-profile physical default route, exact-IP binds, manages only owned scoped TCP/UDP rules, and fails closed on refresh/rebind errors | Connected |
| Android transport | Existing Android network package | Strict QR, pinned TLS 1.3 client, AndroidKeyStore signer, minimal binding store, auth/re-auth, exact-bound-device rediscovery, repository refresh/offline/unpair, and bounded reads/actions | Implemented; local compilation proof blocked by host Java 8 |

## Reuse constraints

- Keep one Core `state.db`, one Evidence Ledger, one `SupervisionService`, and
  one Recovery implementation.
- Device Link projects Core facts through bounded DTOs and delegates
  `APPROVE_ONCE`/`REJECT` to `SupervisionService`; it is not another authority.
- Android stores only its own non-exportable key and Desktop binding metadata.
- No visual Desktop or Compose source is part of this closure.

## Validation boundary

- Python Device Link targeted tests: 125 passed after closure.
- The single full-suite run completed with 898 passed and 29 failed. Seven
  failures were stale expectations for the new observation field/migration v8
  and now pass in a 21-test focused rerun; one loopback test connection reset
  passed on focused rerun. The remaining 21 are existing Windows/POSIX test
  environment mismatches (symlink privilege, `os.fchmod`/POSIX modes and path
  semantics, `/tmp`, and absent `bash`/`ls`/`pwd`), not failures in this closure.
- Android Gradle dependency resolution reached the Android Gradle Plugin, then
  stopped before source compilation because this host supplies Java 8 while the
  configured plugin requires Java 11 or newer. No host tooling was installed or
  changed to manufacture validation.
- Real Android hardware pairing, network-change reconnect, and LAN firewall
  reachability remain real-device verification, not backend implementation work.
