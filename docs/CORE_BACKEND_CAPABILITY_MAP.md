# Core Backend Capability Map

Closure base: `48f37448c3a9af546368fef3bc52bdfc39bc7633`

## V1 Host-native closure

| Capability | Authority and implementation | Product result |
|---|---|---|
| Host-native discovery | `ProductDiscoveryService` and `PsutilProcessBackend` resolve a unique safe native CWD/git root; the binding is persisted by `WorkspaceScopeService` | Native Codex/Claude workspace protection does not require Docker or WSL |
| Workspace scope | Exact root is Core-only; projections and Ledger use opaque workspace IDs, target refs, and digests | Caller cannot supply a path, domain, manifest, or coverage policy |
| Coverage/manifest | `workspace_policy` classifies every bounded observation as `restorable`, `audit_only`, `excluded`, or `unreachable`; Snapshot V3 binds the coverage digest and restorable blobs | Exclusions, inaccessible objects, permission limits, and incomplete scans remain explicit |
| Workspace Changes/Evidence | `workspace_changes` compares the latest matching checkpoint to a fresh scan and records safe create/modify/delete facts | Changes are `UNATTRIBUTED` unless stronger causal evidence exists; Created is not automatically Recoverable |
| Test Restore | `HostWorkspaceRecoveryAdapter` materializes only the authorized restorable set in isolation and verifies hashes plus current-user permission proof | Non-mutating `TEST_RESTORE_VERIFIED` or an explicit fail-closed reason |
| Workspace Restore | The adapter preflights scope/coverage, quarantines post-checkpoint restorable regular files, restores the baseline in two phases, and verifies the resulting manifest/hash/permission state | Full proof is `WORKSPACE_RESTORED_AND_VERIFIED`; residue or incomplete verification is explicitly degraded |
| Windows permissions | DACL SDDL/file attributes are captured, applied, and verified only when reliable under the current user | Capability absence is a target/coverage failure or degradation; no Recovery UAC path exists |
| Desktop SAS | `PairingSession` stores the authority SAS and exposes it to loopback Desktop only while `sas_pending` | Desktop and Android compare the same server-owned value; frontend does not compute it |
| Device Link firewall | `ScopedFirewall` invokes a fixed-operation mode in the installed Desktop executable through UAC; the helper rechecks the exact physical Private-LAN scope and mutates only ASG-owned TCP/UDP 8788 rules | Ordinary MSI users need no manual PowerShell/netsh; decline/scope change/helper failure remains fail-closed and no network profile is changed |

No K3 presentation source is modified by this closure.

## Earlier product call graph retained

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
| Snapshot/recovery | `SnapshotStore`, `RecoveryService`, `RecoveryCoverageService`, `SelfRuntimeAdapter`, `HostWorkspaceRecoveryAdapter` | Full summary plus server-owned host-workspace checkpoint/test/confirmed restore actions, with product-config fallback only when no workspace is observed; actual restore proof derives only from verified full restore evidence | Connected; no second recovery system |
| AI provider | `OpenAICompatibleProvider` | Provider test/models stay existing; analyze is intent-only and Core-built; latest sanitized advisory is projected to 8788 | Connected as advisory only |
| Device crypto/pairing | Existing P-256 helpers, SAS transcript, `PairingManager`, `PairingSession` | One-time QR ticket, pairing token, dual confirmation, domain-separated mutual auth, re-auth, and token invalidation extend the existing gateway | Productized without replacing primitives |
| Desktop identity | Existing P-256 serialization primitives | Stable UUID plus distinct signing/TLS keys in product state; current-user DPAPI on Windows and mode-0600 development fallback | Durable |
| Device binding | Core SQLite migration system | Minimal public binding metadata and revocation state in migration 8; live credentials remain memory-only | Durable and restart-safe |
| Device gateway | Existing gateway primitives | Independent allowlisted TLS 1.3 FastAPI/uvicorn app on 8788; Core 8787 no longer mounts `/device/v1` | Separated |
| Listener/network | Windows route/adapter/firewall system observations plus the fixed product-owned elevated helper | Disabled-by-default controller selects one private-profile physical default route, applies exact-IP/subnet TCP/UDP 8788 rules through scoped UAC, then binds; refresh/rebind/cleanup errors fail closed and profile switching is absent | Connected at source/Python contract level; packaged Windows UAC execution still requires MSI dogfood evidence |
| Android transport | Existing Android network package | Strict QR, pinned TLS 1.3 client, AndroidKeyStore signer, minimal binding store, auth/re-auth, exact-bound-device rediscovery, repository refresh/offline/unpair, and bounded reads/actions | Implemented; local compilation proof blocked by host Java 8 |

## Reuse constraints

- Keep one Core `state.db`, one Evidence Ledger, one `SupervisionService`, and
  one Recovery implementation.
- Device Link projects Core facts through bounded DTOs and delegates
  `APPROVE_ONCE`/`REJECT` to `SupervisionService`; it is not another authority.
- Android stores only its own non-exportable key and Desktop binding metadata.
- No visual Desktop or Compose source is part of this closure.

## Validation boundary

The historical counts below describe the earlier audit and are not validation
claims for this closure. Current closure evidence is reported by its own commit
and verification run.

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
