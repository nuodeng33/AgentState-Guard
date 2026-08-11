# Device Link v1 Implementation Contract — LAN Sync + One-Time QR Authorization

Date: 2026-08-11
Contract status: implementation-ready design; product implementation is **not authorized until R4 closes**
Audited frozen R4 candidate: `f9731426e1d4433aeeb5ec50b6d887f705ab8b42`
Reported K3 branch/SHA: `feat/product-ui-i18n-k3` / `822acf6dbaf00f74879085cb88f160273971c7dd` — excluded from this audit and **not verified by this contract**

## 1. Authority, scope, and precedence

This document is the implementation contract for the first production Device Link release after R4 closes. It is based on the frozen source tree above. It supersedes conflicting Device Link planning documents for the v1 subjects covered here: network boundary, pairing authorization, durable trust, reconnect discovery, Android permissions, and read projections. It does not alter R4, K3, Device Link production code, Evidence semantics, deterministic policy, recovery authority, or AI authority.

The v1 boundary is fixed:

- Core is local authority and listens on `127.0.0.1:8787` only.
- Device Link is a separate, allowlisted gateway on exactly one current physical LAN IPv4 address and fixed port `8788`.
- Neither listener may bind `0.0.0.0`.
- The gateway is not an HTTP reverse proxy and never exposes arbitrary Core routes.
- Android is read-only. Pairing and authentication messages establish a transport identity; they do not grant product mutation authority.
- One Android device is supported in v1.
- First trust uses one short-lived QR ticket, the existing P-256/SAS antecedent, and exactly one explicit user-mediated match/reject decision. That decision is never automatic; its UI/API placement remains TBD and does not grant Android product mutation authority.
- Once successfully bound, normal reconnect uses the durable identities and never repeats QR, SAS, or manual confirmation.

Implementation must wait for R4 close. K3 is presentation-only by reported intent; it was deliberately not audited, checked out, merged, or modified here. This contract does not depend on K3 and does not authorize a merge from or edit to it.

## 2. Current code inventory at the frozen SHA

Status terms in this section describe source that was actually inspected, not intended behavior.

### 2.1 Inventory matrix

| Area | Status | Current evidence and consequence |
| --- | --- | --- |
| P-256 primitives | IMPLEMENTED | `agentguard/device_link/crypto.py` generates and validates P-256 keys, exports DER SPKI, signs/verifies ECDSA-SHA256, and computes full SHA-256 SPKI fingerprints. Reuse these primitives. |
| Secure randomness and SAS antecedent | IMPLEMENTED | The same module generates 16-byte session IDs and 32-byte tokens and contains the existing six-digit SAS primitive. Reuse is preferred, but this contract does not redefine the QR ticket as its HMAC key or freeze a new cross-platform SAS derivation. |
| Pairing FSM/expiry | PARTIAL | `pairing.py` has bounded in-memory sessions, exact `now >= expiry` handling, terminal states, and secret clearing. It lacks a QR authorization ticket, durable identity transition, independently reproducible transcript, and explicit user-mediated confirmation boundary. |
| Pairing HTTP flow | PARTIAL / UNSAFE | `gateway.py` and `api/server.py` expose start, connect, SAS, confirm, and complete. A LAN caller could start pairing, ask the server for the SAS, send `confirm=true`, and bind its own key. This is not an acceptable first-trust ceremony. |
| Challenge authentication | PARTIAL | Device-bound one-use challenge records, Android signature verification, bearer sessions, and revoke-driven token invalidation exist in memory. The signed value is only the raw challenge, authentication is not mutual, failed signatures do not consume the challenge, and nothing survives restart. |
| Device registry | PARTIAL | Registry metadata and read permission exist, but all records are process-local dictionaries; default configuration permits multiple devices. |
| Production gateway boundary | MISSING | `/device/v1` is mounted into the same FastAPI app as Core. There is no separate app/listener, no `8788` desktop listener, no route allowlist boundary, and no gateway-to-projection adapter. |
| Core listener boundary | PARTIAL | Packaged desktop starts Core on `127.0.0.1:8787`, but CLI `serve --allow-remote` can expose the complete Core app on `0.0.0.0:8787`. Device Link v1 must not use that path. |
| Production transport security | MISSING / TBD | Current Android transport is cleartext HTTP and only permits the emulator host in its network-security config. The production transport security profile is intentionally deferred to a separate bounded review; it must provide authenticated confidentiality, integrity, downgrade resistance, and binding to the durable identities before ticket or token disclosure. |
| Stable desktop identity | MISSING | `create_app()` generates a new desktop UUID and private key on each creation. There is no protected durable identity store. |
| QR payload | PARTIAL | Android `QrPayload.kt` splits a permissive URI into host, port, UUID, fingerprint, session, and expiry. It has no protocol version or ticket, and does not enforce canonical form, expiry, exact IPv4, fingerprint length, duplicates, or unknown fields. |
| Android scanner/pair UI | MISSING | Manifest has no camera permission and the active Compose application has no scanner, pairing repository, ViewModel, or SAS screen. |
| Android durable identity | MISSING | No Android Keystore production key, DataStore/Room binding, persistent endpoint, restart auth, transport-security binding, or revocation handling exists. The only EC key is transient test code. |
| Android screens | UI-ONLY / MOCK | The five-screen shell is not wired to `DeviceLinkClient`. Environment values are hardcoded samples; Home is default state; Changes/checkpoints are empty; AI says not configured. Fake production data must be removed, not preserved as fallback. |
| Status/checkpoints | MOCK/NOOP | Production status exposes only minimal process-memory counts. Checkpoints always returns an empty list. |
| Environment/changes/AI routes | MISSING | Android declares calls, but production does not mount those routes. Because the Core SPA catch-all excludes only `/api`, unknown `/device/v1/...` GETs can return HTML 200; a separate gateway must eliminate this behavior. |
| Read-side authority | IMPLEMENTED, REUSABLE INTERNALLY | `R4ReadProjectionService` already returns sanitized, fail-closed runtime, agents, supervision, and recovery projections from verified server-owned state. The gateway must adapt this service internally rather than expose raw Core schemas or proxy Core HTTP. |
| LAN Sync | MISSING | No executable adapter discovery, IPv4/prefix/subnet calculation, scoped firewall ownership, rollback, generation state, or network-change resync exists. See section 7. |
| Endpoint candidate enumeration | DEFERRED / TBD | Android has no implementation. Transport selection is intentionally assigned to a separate bounded design gate; section 8 freezes only its trust/interface/test boundary. This is not a missing contract deliverable. |

### 2.2 Tests that exist and what they do not prove

The frozen tree contains Python suites for audit, crypto, pairing, security regressions, production API, integration, and E2E, plus Android parser/client/instrumented tests. Useful coverage includes P-256 DER validation, fingerprinting, deterministic SAS, exact TTL boundaries, terminal secret clearing, identity-substitution rejection, challenge replay/device binding, bearer checks, and completion-time expiry races.

They do **not** prove the v1 boundary. Most integration/E2E tests use hand-built HTTP routers rather than the production FastAPI app. The Android JVM transport test uses an always-200 server. The instrumented test uses cleartext HTTP, obtains SAS from the server, uses a transient software EC key, and does not test scanner, Keystore, production transport security, challenge auth, restart, projections, or reconnect. No frozen test proves a physical-LAN-only `8788` listener, Core non-exposure, authenticated confidential transport, durable binding, independent SAS, Windows firewall scoping, or real projection data.

### 2.3 Existing documentation conflicts

The current Device Link documents disagree on P-256 versus Ed25519, HMAC versus HKDF, whether mDNS is required or deferred, and whether write/checkpoint/restore/events are in scope. Some environment and closure documents claim Android persistence, discovery, or production projection routes that source inspection does not support. For this v1 implementation, source reality plus this contract take precedence. Older documents remain historical and must not be used to reintroduce mobile mutations or a broad remote Core listener.

## 3. Required component boundary

```text
Tauri WebView / desktop UI
        |
        | loopback HTTP, Core session auth
        v
Core authority: 127.0.0.1:8787
        |
        | in-process typed projection/coordinator interface only
        | (never a transparent HTTP proxy)
        v
Device Link gateway app
  scoped gateway service <selected-physical-IPv4>:8788
  transport-security profile: TBD / separate bounded gate
  endpoint candidate enumeration: TBD / separate bounded gate
        ^
        | current subnet only, pinned identities
        |
Bound Android device (read-only)
```

The Core and gateway may run in the same sidecar process, but they must remain separately bound services with isolated interfaces and operation allowlists. If the later transport review selects HTTP/ASGI, they must be separate ASGI applications with separate sockets and route tables. Sharing a process never permits mounting Device Link into Core or transparently proxying Core operations.

### 3.1 Core requirements

- Bind only IPv4 loopback `127.0.0.1:8787` in packaged/product execution.
- Continue to be the sole local mutation and policy authority.
- Expose authenticated loopback coordination for pairing session creation, cancellation, and status polling. The single user-mediated SAS decision is never automatic; its final UI/API placement is selected by a later bounded review.
- Never expose the QR ticket, `/api/session`, arbitrary `/api/*`, recovery mutations, supervision mutations, AI provider configuration, or secrets as read projections on `8788`.
- Device Link implementation must not invoke or rely on the current `--allow-remote` Core mode. Product tests must fail if the Device Link path makes Core reachable from a non-loopback socket.

### 3.2 Gateway requirements

- Bind the production gateway service only to the adapter facts returned by LAN Sync at exact IPv4:`8788`. Candidate-enumeration listeners/transport are not selected or authorized by this contract. No wildcard bind, fallback bind, secondary interface, IPv6 listener, or gateway port selection is allowed.
- `TRANSPORT_SECURITY_PROFILE = TBD / SEPARATE BOUNDED REVIEW`. Before D4/D5 transport code, that review must select and threat-model the mechanism, Android platform/provider floor, peer-authentication material, downgrade policy, wire framing, and tests. Whatever is selected must authenticate the intended durable peer, provide confidentiality and integrity before any ticket/token/read data is sent, resist downgrade/replay, fail closed, and remain bound to the fresh challenge and exact IPv4:`8788`. Native Android is the only client. If HTTP/browser-origin semantics are selected, the implementation must add no broad CORS and must reject browser-origin use.
- Do not serve desktop UI assets. Unknown logical operations fail with a typed bounded error. If HTTP is selected, every Core `/api/*` path is unavailable and unknown paths return a typed 404.
- Expose only bounded pairing claim/result operations, fresh challenge authentication, and the read-only projection operations defined in section 10.
- Enforce one bound device, message/field limits, per-session and per-source rate limits, exact schema versions, and stable error semantics.
- Obtain data through a narrow `DeviceProjectionSource`; never forward caller-controlled operation names, metadata, or payloads to Core.

### 3.3 WebView, CSP, CORS, and capability boundaries

The Tauri WebView continues to call only Core loopback `127.0.0.1:8787`. It does not call the LAN gateway. Consequently Device Link does not require adding the dynamic LAN gateway to WebView CSP, does not require wildcard `connect-src`, and does not require any CORS expansion. No Tauri capability or remote-content permission is needed for Android networking. Any implementation that broadens CSP, adds wildcard CORS, changes `allow_remote`, or exposes `8787` to LAN fails this contract.

## 4. Durable identities and key separation

V1 uses distinct keys for distinct purposes:

| Identity/material | Lifetime and storage | Purpose |
| --- | --- | --- |
| Desktop UUID | Stable per desktop installation; protected durable desktop state | Selects the intended already-known desktop. It is an identifier, not a secret. |
| Desktop P-256 identity key | Stable; private key protected with Windows user-scoped facilities; public DER SPKI persisted | Signs pairing and fresh auth responses. Its SHA-256 SPKI fingerprint is the durable trust anchor. |
| Transport-security identity/material | Exact mechanism and storage are selected by the separate bounded transport-security review | Must remain separate from the P-256 identity key if the chosen profile introduces distinct long-term material, and must satisfy authenticated-confidential transport properties without becoming a second product authority. |
| Android UUID | Stable per app installation/binding record | Identifies the single Android device. |
| Android P-256 identity key | Non-exportable Android Keystore key | Signs pairing possession proofs, finalization, and auth responses. |
| QR authorization ticket | 32 random bytes, memory-only during one pairing ceremony | One-time first-pair capability. It is never a durable trust anchor and this contract does not define it as the SAS key. |
| Pairing flow token | Random, session-bound, expiry-bound, temporary | Authorizes polling/finalization after the ticket has been claimed. It grants no read projection access. |
| Read session token | Random 32-byte bearer, memory-only, one hour maximum | Short-lived gateway read authentication after a successful mutual challenge. |

Desktop UUID, desktop identity key, the one bound-device record, and any durable transport-security material selected later must survive Core/gateway restart. Pairing sessions, raw tickets, SAS values, challenges, and bearer tokens must not survive restart. Restart before a completed binding outcome cancels the ceremony; restart after a completed binding uses fresh challenge authentication, never a new QR.

## 5. Canonical QR contract

### 5.1 URI and canonical encoding

The only accepted URI form is:

```text
agentstate://pair/v1?protocol_version=1&host=<ipv4>&port=8788&desktop_uuid=<uuid>&desktop_identity_fingerprint=<hex64>&pairing_session_id=<hex32>&expires_at=<unix-seconds>&authorization_ticket=<base64url43>
```

Rules:

- UTF-8 input must decode to ASCII only and be at most 512 bytes.
- Scheme, authority, path, field names, and field order are exact and lowercase: scheme `agentstate`, authority `pair`, path `/v1`, then the eight query fields shown above.
- There is no fragment, user information, password, extra path segment, blank value, duplicate field, or unknown field.
- Values use only their field-specific ASCII alphabets. Percent encoding, `+`, whitespace, control characters, Unicode confusables, alternate IP spelling, and a trailing delimiter are rejected. A parser must serialize the parsed structure and require byte-for-byte equality with the input.
- Parsing precedence is deterministic. First enforce ASCII/length and the outer `agentstate://pair/v<minimal-decimal>` grammar. Then parse `protocol_version` as minimal decimal: disagreement between path and query versions is `QR_MALFORMED`; equal, syntactically valid versions other than `1` are `PROTOCOL_UNSUPPORTED`. Only after version classification are all remaining fields validated. Every local QR failure causes **no network request**.

### 5.2 Field definitions

| Field | Exact type and validation |
| --- | --- |
| `protocol_version` | ASCII decimal integer exactly `1`; no sign or leading zero. Unknown values are not downgraded or guessed. |
| `host` | Canonical dotted-decimal IPv4, 7–15 bytes, each octet minimal decimal. It must equal the QR-generating physical adapter address, be unicast RFC1918, and be neither loopback, unspecified, multicast, link-local, network address, nor directed broadcast. Android also requires it to be on its current local subnet before connecting. |
| `port` | ASCII decimal exactly `8788`; no default and no alternate port in v1. |
| `desktop_uuid` | Canonical lowercase RFC 4122 UUID string, 36 bytes. It is stable for the desktop installation. |
| `desktop_identity_fingerprint` | Exactly 64 lowercase hexadecimal characters: SHA-256 of the desktop P-256 DER SPKI. Short fingerprints are display-only and never accepted here. |
| `pairing_session_id` | Exactly 32 lowercase hexadecimal characters representing 16 CSPRNG bytes. |
| `expires_at` | Unsigned Unix UTC seconds in minimal decimal, at most 10 digits. Session lifetime is 120 seconds. Validity is `server_now < expires_at`; equality is expired. Android may reject locally using its clock but may never extend server expiry or add grace. |
| `authorization_ticket` | Exactly 43 base64url characters without padding, decoding to 32 CSPRNG bytes (256 bits). Standard base64, padding, and non-canonical encodings are rejected. |

The QR is a user-mediated bootstrap capability. It is not encrypted; adding ad-hoc encryption would not hide it from a camera that can read the ticket and would create a second key-distribution problem. The selected transport-security profile must authenticate the intended desktop and establish confidentiality and integrity before Android submits the ticket. Whether that profile needs additional versioned QR binding material is decided only by its bounded review; no TLS-specific QR field is frozen here.

### 5.3 Ticket lifecycle

The server keeps the raw 32-byte ticket only in the in-memory pairing session, compares submitted ticket bytes in constant time, and clears them on every terminal outcome. It is never written to the durable device registry, logs, Evidence payloads, analytics, crash reports, or Android durable binding state. This contract intentionally does not redefine that ticket as `pairing_secret` or freeze ticket-to-HMAC SAS derivation.

| Ticket/session state | Meaning | Allowed next state |
| --- | --- | --- |
| `ISSUED` | Created by an authenticated user-mediated start action; QR visible; no Android identity trusted. | `CLAIMED`, `REJECTED`, `EXPIRED`, `CANCELLED` |
| `CLAIMED` | First valid ticket redemption plus Android P-256 possession proof accepted. Ticket is immediately non-redeemable; a temporary flow token replaces it. | `SAS_PENDING`, `REJECTED`, `EXPIRED`, `FAILED` |
| `SAS_PENDING` | Both peers have independently derived/displayed the same transcript-bound SAS. | `USER_CONFIRMED`, `REJECTED`, `EXPIRED`, `FAILED` |
| `USER_CONFIRMED` | The user made the ceremony's one explicit match decision through the later-approved UI/API placement. It is never automatic and is still not durable trust. | `BOUND`, `EXPIRED`, `FAILED` |
| `BOUND` / ticket `CONSUMED` | An atomic transaction stored the one read-only Android binding and consumed/cleared the ticket. Durable device identity begins here. | normal challenge auth; local revocation |
| `REJECTED` | The user selected reject through the later-approved live confirmation surface, or either peer explicitly cancelled after claim. | terminal; ticket invalid |
| `EXPIRED` | `now >= expires_at` at any transition. | terminal; ticket invalid |
| `FAILED` / `CANCELLED` | Validation, persistence, restart, capacity, or local cancellation ended the ceremony. | terminal; ticket invalid |

Only one claim can win. Concurrent, reordered, duplicate, wrong-session, or replayed tickets must produce stable failure codes and cannot advance state. A claim for another session is not treated as a claim for the QR session. Reject, expiry, cancellation, or failure immediately invalidates the ticket and clears both the raw ticket and derived SAS material. Successful binding consumes the ticket in the same atomic outcome that commits the device record; failure to commit leaves no binding and no reported success.

## 6. Pairing protocol and durable-trust transition

### 6.1 Local start

An authenticated desktop WebView asks Core loopback to create a pairing session. Core refuses if a device is already bound, LAN exposure is unavailable/degraded, or a non-terminal pairing already exists. Core obtains the current LAN endpoint, creates the 120-second ticket/session, and renders the canonical QR. There is no unauthenticated `pair/start` on `8788`.

### 6.2 Claim and P-256 possession proof

After strict local QR validation, Android connects to the exact QR IPv4:`8788` through the production transport-security profile selected by its later bounded review. That profile must authenticate the intended peer and provide confidentiality, integrity, downgrade resistance, and fail-closed behavior before Android sends the ticket or any token; this contract does not select its protocol, provider, certificate model, wire framing, or Android API floor.

Android verifies the full desktop P-256 DER SPKI against the QR desktop-identity fingerprint, then submits the exact session ID, one-time ticket, stable Android UUID and P-256 DER SPKI, a fresh Android nonce, bounded display name, and P-256 possession proof over a canonical claim transcript. Desktop returns its fresh nonce, exact transcript facts, an expiry-bound pairing-flow token, and a P-256 response proof. Both proofs bind the protocol/session, both UUIDs and public identities, nonces, expiry, and exact IPv4:`8788`. Wrong identity, endpoint, session, ticket, signature, expiry, duplicate claim, or replay is terminal and creates no trust.

### 6.3 Existing SAS and transcript invariants

V1 reuses the frozen P-256 and SAS antecedents. It does not define a new SAS cryptosystem, redefine the QR authorization ticket as an HMAC key, or freeze a new KDF. Before D1 implementation, a bounded cryptographic review must confirm the exact existing derivation inputs and produce literal Python/Kotlin vectors without changing the established SAS primitive.

Both peers derive and display the SAS independently; neither accepts a SAS supplied as authoritative by the other. The canonical transcript unambiguously binds protocol/session identity, both durable P-256 identities, both fresh nonces, expiry, and exact IPv4:`8788`. Session mismatch, transcript mutation, SAS mismatch/reject, expiry, duplicate claim, and replay fail closed. The one-time QR ticket remains session-bound, single-use, cleared on every terminal outcome, never persisted, and is not assumed here to be the SAS key. D1 freezes exact framing and vectors only after the bounded review.

### 6.4 One explicit user decision and binding outcome

The ceremony requires exactly one explicit user-mediated `MATCH` or `REJECT` action. It is never inferred, timed, retried, or completed automatically. Final UI/API placement is TBD under a bounded review; it may be coordinated through the authenticated desktop UI or a narrowly authenticated pairing surface on Android. Android remains read-only for product state: this ceremony action can authorize only the sole Device Link binding and cannot approve, reject, restore, checkpoint, or mutate any product fact.

`BOUND` may be reported only after the live session, unexpired claimed ticket, exact transcript, P-256 possession proofs, and explicit user match all verify. The outcome must atomically persist the one read-only binding and consume the ticket, be idempotent for the same session/key, reject a different session/key, fail closed on storage/process/transport uncertainty, and prevent split success. Neither peer may report or persist `ACTIVE` while authoritative outcomes disagree or one is absent.

The recovery mechanism is TBD. This contract does not mandate a signed binding receipt, receipt-recovery/ack routes, or a particular uncertain-finalize state. A later bounded transaction/recovery review must prove atomicity, idempotency, fail-closed behavior, crash/restart handling, and no split-success outcome before implementation.

### 6.5 Pairing/auth surface and encoding invariants

Core remains on `127.0.0.1:8787`; the separate allowlisted gateway remains on the selected physical IPv4:`8788`. Core owns authenticated local coordination. The gateway exposes only bounded pairing claim/state/finalize, fresh challenge authentication, and the read-only projections in section 10. It never exposes arbitrary Core routes or Android product mutations. Pairing and read authorizations are distinct and non-interchangeable.

Exact transport framing and finalize/recovery wire shapes are selected only by their bounded reviews. Whatever is selected must use strict versioned schemas, reject duplicate/unknown/missing/non-canonical fields and trailing content, cap bodies/responses/concurrency/rates/timeouts, and never log or persist tickets, SAS values, bearer tokens, private keys, or raw product data.

P-256 possession proofs and fresh mutual challenge authentication bind both durable UUIDs and public identities, exact candidate IPv4:`8788`, protocol version, nonce, and expiry. Challenges are bounded, expire exactly, and are consumed on the first response attempt including failure. Replay, endpoint substitution, cross-device/session use, revoked identity, or bad signature yields no token and no endpoint update. Read bearer tokens are memory-only, read-scoped, at most one hour, invalidated by restart/expiry/revocation, and renewed by fresh challenge rather than QR/SAS.

No confirmation route or UI placement is frozen. Any later confirmation surface requires a live pairing session plus contemporaneous user interaction, is never callable by reconnect automation, and preserves Android's read-only product boundary.

## 7. LAN Sync adapter contract

### 7.1 Repository search result: ABSENT

No existing LAN auto-sync executable exists in the frozen commit. A supplementary search of accessible workspace copies and Git history at audit time also found no candidate implementation. Search covered adapter enumeration, IPv4/prefix/subnet computation, Windows network-change APIs, firewall cmdlets/COM/netsh, owned rule state, rollback, and resync signatures. The matches in product provenance are design documents, not executable code.

Therefore the future worker must not pretend to integrate an existing program or reconstruct one from memory. It must implement only the narrow adapter below and prove the Windows behavior with integration tests.

### 7.2 Narrow interface

Conceptually, the platform adapter exposes only:

```text
LanEndpoint current_endpoint()
LanExposure enable(port = 8788)
LanExposure resync()
LanExposure disable()
```

It is not a generic network or firewall manager. Each successful result is an immutable snapshot containing:

| Fact | Contract |
| --- | --- |
| `schema_version` | Exact string `device-link-lan/1`. |
| `generation` | Unsigned diagnostic counter that changes whenever interface, address, prefix, subnet, gateway listener, or firewall applied state changes. It appears in authenticated status/auth responses but is not trust or a cross-restart anti-rollback value. |
| `interface_id` | Stable Windows adapter GUID. |
| `interface_index` | Current numeric index, diagnostic only. |
| `interface_kind` | `ETHERNET` or `WIFI`; no tunnel, loopback, VPN, virtual switch, emulator, container, or overlay adapter. |
| `local_ipv4` | Canonical selected unicast IPv4. |
| `prefix_length` | OS-reported prefix; v1 accepts `8..30`. |
| `subnet_cidr` | Canonical network/prefix computed from the address and prefix. |
| `port` | Exactly `8788`. |
| `network_profile` | `PRIVATE` only in v1. Public, unknown, captive, disconnected, and ambiguous profiles fail closed. |
| `tcp_listener_state` | `DISABLED`, `BOUND`, or `FAILED`. A successful bind must verify the socket local address equals `local_ipv4:8788`. |
| `firewall_state` | `NOT_APPLIED`, `APPLIED`, `DEGRADED`, or `ROLLBACK_FAILED`. |
| `owned_rule_ids` | Exact IDs of rules created by this product generation; never inferred by display-name wildcard. |
| `observed_at` | UTC RFC 3339 timestamp for diagnostics, not trust. |
| `state` / `reason_code` | `AVAILABLE` only when listener and firewall facts agree; otherwise a stable fail-closed reason. |

### 7.3 Selection and lifecycle

The selector accepts exactly one adapter that is up, physical Ethernet/Wi-Fi, has an RFC1918 unicast IPv4 and prefix, has a Windows `PRIVATE` connection profile, and is the eligible default-route interface. Zero candidates returns `LAN_UNAVAILABLE`; more than one returns `LAN_AMBIGUOUS`. V1 provides no manual-address normal path and does not guess between adapters.

The Python sidecar gateway runtime is the sole owner of the production gateway service on port 8788. The narrow Windows LAN adapter supplies verified interface facts and owns only the exact firewall policy/rule handle; it neither accepts nor forwards application bytes. If the later-approved service transport requires an inbound rule, that rule is limited to the exact sidecar executable, exact local IPv4, service port 8788, current remote subnet CIDR, and Private profile. No rule may use Any profile, Any program, Any local address, Any remote address, a port range, or `0.0.0.0`. The adapter removes or replaces only rules whose exact owned IDs are in its durable state; it never edits unrelated rules. Any future enumeration rule/listener belongs to the separate design gate and is not authorized here.

On network change, the Python runtime first marks the old exposure unavailable and closes its old gateway listener. The adapter resolves a fresh complete fact set and applies/verifies any new owned rule required by the approved service transport; the Python runtime starts the exact gateway listener and reports its actual local endpoint back for adapter verification before publishing the new exposure/generation. Superseded owned rules are then removed. Any error leaves the gateway unavailable and performs bounded rollback; it must not widen a rule or wildcard-bind as fallback. `disable()` coordinates listener close and removal of only owned rules, is idempotent, and reports residual/rollback failure explicitly.


## 8. Bound-device endpoint candidate boundary

`ENDPOINT_ENUMERATION_TRANSPORT = TBD / SEPARATE BOUNDED DESIGN GATE`

This contract selects no endpoint-enumeration transport. mDNS/Android NSD, generic device discovery, Internet/cloud directories, and manual-IP production fallback are excluded for all v1 production. Until the gate is approved, no other enumeration mechanism is enabled; D1–D6 may define only the transport-neutral boundary below.

### 8.1 Frozen trust invariant

Enumeration establishes reachability only. A candidate address, source, response, local-subnet location, saved hint, or successful connection is not trust and cannot create or renew a binding. The durable selector contains only the saved desktop UUID, full P-256 public key/fingerprint, supported protocol version, optional verifier material required by the later-approved transport-security profile, and last-known IPv4:`8788` as a hint.

Only the endpoint verifier may produce a `VerifiedEndpoint`: it must complete the approved transport-security check and a fresh endpoint-bound mutual P-256 challenge against the saved durable identity. The challenge binds both UUIDs and public identities, protocol, exact candidate IPv4:`8788`, nonce, expiry, and the selected transport-security context. Failure leaves binding and saved endpoint unchanged.

### 8.2 Transport-neutral interface

```text
BoundEndpointCandidateEnumerator.enumerate(
    selector: BoundDesktopSelector,
    scope: LocalNetworkScope,
    bounds: EnumerationBounds,
    cancellation: CancellationToken
) -> BoundedSequence<EndpointCandidate>

VerifiedEndpoint EndpointVerifier.verify(
    candidate: EndpointCandidate,
    binding: BoundDesktopBinding,
    fresh_challenge: MutualChallenge
)
```

Candidates contain only canonical IPv4, fixed port `8788`, opaque mechanism evidence, and observation time. The enumerator cannot access private keys, persist an endpoint, issue tokens, create trust, trigger pairing, or return a generic device list. Only the repository may atomically persist a verified result.

### 8.3 Frozen bounds and tests

Every later transport is foreground-only, current-subnet-only, cancellable, bounded to one invocation, five seconds, concurrency 16, and 64 candidates. It creates no permanent Android scanner/listener, background service, cross-subnet/Internet/cloud traffic, trust by name/address, QR/SAS/confirmation during reconnect, or endpoint write before verification. Its separate gate must define OS APIs, wire format, permissions, firewall/listener ownership, spoof/replay model, packet/rate limits, cleanup, and real Windows/Android tests.

Shared verifier tests inject wrong subnet, UUID, desktop key/fingerprint, transport-security verifier, signature, challenge context, expiry, replay, and revocation; all must leave endpoint and binding unchanged. Only the candidate that completes the approved transport-security check plus fresh challenge may update the endpoint atomically. Timeout, cancellation, limit overflow, and disabled enumeration return truthful unchanged state. With no approved enumerator, reconnect returns `ENDPOINT_ENUMERATION_TBD`.

## 9. Post-bind authentication and reconnect

Normal reconnect first tries the saved IPv4:`8788` through the later-approved transport-security profile, then runs a fresh mutual P-256 challenge against the saved durable identities. If the endpoint is unreachable, Android may invoke only an independently approved section 8 enumerator; without one it reports `ENDPOINT_ENUMERATION_TBD` and preserves trust and endpoint state. Reconnect never launches QR, SAS, or confirmation.

Each challenge uses 32 CSPRNG bytes and binds protocol version, challenge ID, desktop and Android UUIDs/public identities, exact candidate endpoint, selected transport-security context, and an absolute expiry no more than 300 seconds ahead. Android and Desktop verify each other's P-256 proof. The challenge is consumed on the first response attempt including bad signature and removed after terminal use. Replay, cross-device use, endpoint substitution, expiry, revocation, or protocol mismatch is denied.

The resulting 32-byte bearer token is memory-only, scoped to the sole device and `read`, bounded to the current gateway generation, and valid for at most one hour. Restart or expiry requires another fresh challenge, not re-pairing. Authenticated local revocation deletes/inactivates the durable binding and immediately invalidates challenges and tokens; a revoked Android cannot verify a candidate or update the saved endpoint.

## 10. Read-only gateway projection contract

### 10.1 Common envelope and truth states

Gateway projection operations are read-only. The object below freezes semantic fields, not JSON or HTTP wire encoding; exact framing is selected by the bounded transport review. If HTTP is selected, projection reads use GET.

```json
{
  "schema_version": "device-link-read/1",
  "projection": "runtime",
  "state": "AVAILABLE",
  "reason_code": "RUNTIME_AVAILABLE",
  "observed_at": "2026-08-11T12:00:00Z",
  "evidence_refs": [],
  "truncated": false,
  "data": {}
}
```

Every projection has exactly one of these states:

| State | Exact meaning |
| --- | --- |
| `AVAILABLE` | The authoritative source was reached and verified and contains usable facts. It does not mean safe. |
| `EMPTY` | The authoritative query succeeded and proved that no matching records currently exist. |
| `UNKNOWN` | The system is reachable but has not established the requested fact. Absence of evidence is not a safe result. |
| `DEGRADED` | The source is reachable but incomplete, stale, or failed verification; only explicitly safe partial fields may remain. |
| `UNREACHABLE` | Android could not obtain an authenticated response because transport or auth failed. This is synthesized by the Android repository and is never fabricated by a reachable gateway response. |

`UNREACHABLE != SAFE`, `EMPTY != SAFE`, and `UNKNOWN != SAFE`. `CHECKPOINT_CREATED` does not imply recoverability. Recovery level `R3` does not imply `trusted_baseline_status == TRUSTED`. The Android UI must render those facts separately and must not infer an overall green/safe status.

All arrays are capped at 100 items, sorted deterministically, and report `truncated=true` when capped. String atoms are allowlisted/bounded; no local paths, commands, raw diffs, file contents, environment variables, raw database rows, exception text, action references, tokens, tickets, provider credentials, or arbitrary ledger payload keys are returned.

### 10.2 Logical operations and minimum DTOs

| Logical operation | Minimum `data` surface and source |
| --- | --- |
| `read.home` | Derived summary only: section `{projection,state,reason_code,item_count}` for runtime, agents, changes, checkpoints, supervision, and AI; `attention_count`; latest checkpoint recovery facts or `null`. No independent safety judgment. |
| `read.runtime` | `items[]` containing only `runtime_type`, `execution_domain_id`, `availability`, `capabilities[]`, `reason_code`, `uncertainty`; plus `environment_components[]` only when backed by verified desktop facts. If no real environment authority exists, return `UNKNOWN` with empty data—never `EMPTY` and never sample versions. |
| `read.agents` | `items[]` containing R4 allowlisted `detected_identity`, `role`, `lifecycle`, `confidence`, `execution_domain_id`, bounded workspace binding status/reference, `reason_code`, `uncertainty`. |
| `read.changes` | Recent verified change metadata only: `change_id`, `observed_at`, `category`, `result`, `execution_domain_id`, optional supervision session ID, uncertainty, and evidence references. No path, before/after content, command, patch, or arbitrary payload. An absent/unimplemented authority is always `UNKNOWN` with empty data; `EMPTY` is allowed only after an implemented authoritative query succeeds and proves zero records. |
| `read.checkpoints` | `items[]` with checkpoint ID, execution domain, authoritative status/reason, requested/authorized/intact/test-restored counts, recovery level, R1/R2/R3 booleans, test-restore status, trusted-baseline status/ID, and evidence refs. Checkpoint labels are omitted unless separately sanitized. |
| `read.supervision` | `items[]` with session ID, status, deterministic policy decision, requires-manual-approval, requires-checkpoint, recorded manual approval fact, bounded AI assessment, recovery facts, and evidence refs. Omit Core `action_ref`; no approve/reject product operation exists on the gateway. |
| `read.ai-advisory` | `deterministic_policy` and `advisory` are separate objects. Minimum advisory fields are state, decision, severity, reason code, and evidence refs from desktop-produced facts. No provider, model endpoint, API key, provider configuration, prompt, or direct mobile AI request. AI may not upgrade deterministic policy authority. |
| `read.connection` | Authenticated server facts: protocol version, desktop UUID, bound Android UUID, permission `read`, gateway LAN generation, and token expiry. Android overlays local states such as connecting, authenticated, unreachable, revoked, and endpoint-changing. |

The initial adapter should consume `R4ReadProjectionService` through typed Python calls. It may map R4 `status` to mobile `state` but must retain R4 `reason_code`, evidence references, uncertainty, recovery level, test-restore status, and trusted-baseline status without optimistic rewriting. It must not pass through `action_ref` or add mobile mutations.

### 10.3 Error vocabulary

Reachable gateway failures use the following bounded semantic envelope; its wire encoding is selected later:

```json
{"schema_version":"device-link-error/1","code":"TICKET_EXPIRED","retryable":false}
```

No raw exception/detail field is permitted. Stable codes include:

- QR/local parsing: `QR_MALFORMED`, `QR_EXPIRED`, `PROTOCOL_UNSUPPORTED`, `ENDPOINT_OUTSIDE_ACTIVE_SUBNET`.
- LAN: `LAN_UNAVAILABLE`, `LAN_AMBIGUOUS`, `LAN_PROFILE_UNSUPPORTED`, `LAN_EXPOSURE_DEGRADED`, `GATEWAY_UNREACHABLE`.
- Pairing: `PAIRING_NOT_ENABLED`, `PAIRING_SESSION_NOT_FOUND`, `PAIRING_SESSION_MISMATCH`, `TICKET_INVALID`, `TICKET_EXPIRED`, `TICKET_ALREADY_CLAIMED`, `TICKET_REPLAYED`, `DESKTOP_IDENTITY_MISMATCH`, `TRANSPORT_SECURITY_IDENTITY_MISMATCH`, `SAS_REJECTED`, `PAIRING_EXPIRED`, `DEVICE_ALREADY_BOUND`, `PAIRING_STATE_INVALID`, `PAIRING_PERSISTENCE_FAILED`.
- Auth: `AUTH_CHALLENGE_EXPIRED`, `AUTH_CHALLENGE_REPLAYED`, `AUTH_SIGNATURE_INVALID`, `AUTH_IDENTITY_MISMATCH`, `DEVICE_REVOKED`, `SESSION_TOKEN_INVALID`, `SESSION_TOKEN_EXPIRED`.
- Endpoint/projection: `ENDPOINT_IDENTITY_MISMATCH`, `ENDPOINT_ENUMERATION_TBD`, `ENDPOINT_ENUMERATION_TIMEOUT` (reserved until a transport gate defines it), `CORE_PROJECTION_UNAVAILABLE`, `PROJECTION_DEGRADED`.

The bounded transport review maps these stable errors to its wire semantics. If HTTP is selected, it must define status mappings during that review; this contract does not freeze them. Network responses must not distinguish unknown-ticket from wrong-ticket in a way that helps enumeration; internal reasons may be more specific.

## 11. Android persistence and repository model

V1 stores exactly one bound-desktop record in typed, atomic app-private storage and one non-exportable P-256 key in Android Keystore. The durable record contains schema version, Android UUID/key alias, desktop UUID/full P-256 DER SPKI/fingerprint, protocol version, last-known canonical IPv4 and fixed port `8788`, optional verifier material required by the later-approved transport-security profile, diagnostic gateway generation, binding timestamp/display name, and local `ACTIVE|REVOKED` state.

It never stores the raw QR URI, ticket, pairing secret, SAS, provider configuration, API keys, read token, or raw Core response. Pending pairing state is expiry-bound and non-authoritative; it cannot authenticate reads or render `ACTIVE`. Its exact crash/restart representation is selected by the later transaction/recovery review and must satisfy section 6.4 without mandating signed receipts or split success.

One repository owns connection state and sealed projection states. It converts transport/auth failures to `UNREACHABLE`, never demo data or SAFE. On restart it tries the saved endpoint and fresh challenge; after failure it invokes only an approved enumerator or reports `ENDPOINT_ENUMERATION_TBD`. Endpoint change never triggers QR/SAS.

### 11.1 Android permission and network boundary

The app retains minimum SDK 26. This contract freezes no API 29+ floor, TLS/HTTPS version, provider, certificate model, or network-security implementation. The bounded transport-security review selects the supported Android API/provider matrix and any mechanism-specific configuration/permissions, and proves authenticated confidentiality, integrity, downgrade resistance, peer binding, fail-closed behavior, and real-device compatibility before D4/D5 transport work.

The frozen manifest surface is limited to `INTERNET`, `ACCESS_NETWORK_STATE`, pairing-time `CAMERA`, and optional camera hardware declaration. Enumeration-specific permissions remain TBD; there is no background location, foreground-service workaround, or permanent scanner. Camera denial/unavailability and missing/ambiguous active-network or RFC1918 IPv4/prefix facts render stable truthful failure and perform no discovery, pairing request, or endpoint update. The scanner is local/offline and never uploads frames.

The repository accepts exactly one active Wi-Fi or Ethernet network that is non-VPN and non-captive, obtains exactly one eligible RFC1918 IPv4/prefix, binds the later-approved transport to that exact network, and requires the QR/verified candidate to lie in the subnet. Cellular, VPN, captive, absent, or ambiguous facts fail closed. It does not read SSID/BSSID or location for pairing/verification.

## 12. Minimum regression and acceptance matrix

Every row is required before the future implementation can claim Device Link v1 behavior. Unit tests use fixed clocks/RNG and cross-language vectors; network assertions require real sockets. Windows listener/firewall behavior and Android Keystore/scanner behavior cannot be replaced by mocks.

### 12.1 Pairing and QR

| ID | Required assertion |
| --- | --- |
| `PAIR-01 valid-ticket` | Canonical QR, approved transport-security check and desktop identity, valid key proof, matching SAS, explicit user match and atomic finalize produce exactly one durable read-only binding and consumed ticket. |
| `PAIR-02 expired-ticket` | `now == expires_at` and later fail; no binding/token; secret cleared. |
| `PAIR-03 replayed-ticket` | Second claim, concurrent claim, and post-bind replay fail without changing the first identity. |
| `PAIR-04 wrong-session` | Valid ticket with another session ID fails and advances neither session. |
| `PAIR-05 malformed-qr` | Wrong scheme/path/order, version disagreement, duplicate/unknown/missing field, percent encoding, overlength, invalid IPv4/UUID/hex/base64, or wrong port yields `QR_MALFORMED`; equal canonical unsupported versions yield `PROTOCOL_UNSUPPORTED`; both cause zero network I/O. |
| `PAIR-06 wrong-fingerprint` | Wrong desktop identity or later-approved transport-security verifier stops before ticket submission/binding. |
| `PAIR-07 sas-reject` | Explicit user-mediated reject through the later-approved live surface makes terminal `REJECTED`, invalidates ticket/session, and leaves no binding. Unauthenticated or automated callers cannot confirm; Android placement is not prohibited. |
| `PAIR-08 sas-expiry` | Expiry before or during user confirmation/finalize fails terminally with no binding. |
| `PAIR-09 successful-bind` | Durable desktop registry and Android record contain exact matching identities/read permission; restart uses auth without QR/SAS. |
| `PAIR-10 sas-vectors` | Python and Kotlin independently produce one literal expected SAS from the exact binary vector; mutating every transcript field changes verification outcome. |
| `PAIR-11 atomicity-and-recovery` | Fault injection at DB/Evidence commit, either durable-store write, response delivery, or either restart produces one authoritative outcome: no binding or the same single active binding on both peers. Retry is idempotent; uncertainty fails closed; no split success, second key, repeated ceremony, or ticket leak occurs; recovery mechanism remains TBD. |
| `PAIR-12 single-device` | A second pairing session is refused while one active device is bound. |
| `PAIR-13 signed-vectors` | Python and Kotlin share literal vectors for the later-approved claim/finalize transcripts and existing P-256/SAS antecedent; changing each field or appending bytes fails verification. |

### 12.2 Authentication

| ID | Required assertion |
| --- | --- |
| `AUTH-01 valid-challenge` | Both signatures, IDs, endpoint, protocol, expiry, and approved transport-security context verify; one read token is issued. |
| `AUTH-02 wrong-signature` | Wrong Android or desktop signature fails; challenge is consumed on first attempt. |
| `AUTH-03 expired-challenge` | Exact expiry boundary fails with no token. |
| `AUTH-04 replay` | Reused, reordered, cross-device, or cross-endpoint challenge fails. |
| `AUTH-05 revoked-device` | Revocation invalidates outstanding challenges/tokens and prevents every candidate from passing endpoint verification. |
| `AUTH-06 expired-session-token` | Read request at expiry returns typed 401; fresh challenge works without QR/SAS. |
| `AUTH-07 restart` | Desktop and Android restarts retain durable identity/binding, discard transient tokens, and reconnect by challenge only. |
| `AUTH-08 signed-vectors` | Python and Kotlin share literal AUTH_CHALLENGE/AUTH_REQUEST/AUTH_RESPONSE bytes and signatures; every field, length, endpoint, transport-security context, token hash, expiry, and trailing-byte mutation fails. |

### 12.3 Network boundary and LAN Sync

| ID | Required assertion |
| --- | --- |
| `NET-01 core-loopback` | Core owns only `127.0.0.1:8787`; LAN address and `0.0.0.0:8787` refuse. |
| `NET-02 gateway-physical` | Gateway service listens only on the selected physical IPv4:`8788`; listener ownership and observed local endpoint match adapter facts. No enumeration listener is implied. |
| `NET-03 no-wildcard` | Socket enumeration proves no `0.0.0.0`/`::` listener for either service. |
| `NET-04 route-isolation` | Every Core-only `/api/*`, session bootstrap, mutation, SPA, and unknown route is unavailable from `8788`; `/device/*` is unavailable from `8787` except authenticated local pairing coordination routes under the Core namespace. |
| `NET-05 firewall-scope` | Any owned rule required by the reviewed gateway transport matches exact program/local IP/remote subnet/8788/Private profile; unrelated rules remain unchanged. Enumeration firewall policy awaits its separate gate. |
| `NET-06 public-or-ambiguous` | Public, zero-candidate, multiple-candidate, virtual/VPN-only, or malformed-prefix conditions disable exposure without fallback. |
| `NET-07 resync-rollback` | DHCP/profile/interface change closes old exposure, increments generation, replaces owned rules, and leaves no old listener/rule; injected failure is fail-closed and reports residual state. |
| `NET-08 native-boundary` | Gateway has no CORS middleware, WebView CSP is not widened for LAN, and the selected transport profile passes downgrade and unauthenticated-cleartext negative tests. |
| `NET-09 android-platform` | The later transport-security review's Android API/provider matrix passes on real devices; no API 29+ or protocol-version requirement is inferred before that gate. Camera grant/deny/unavailable and allowed Wi-Fi/Ethernet versus cellular/VPN/captive/zero/multiple IPv4 facts produce exact fail-closed states. No enumeration-specific permission appears before its gate. |
| `NET-10 limits` | Every body, field, concurrency, rate, timeout, and response-size boundary in section 6.5 passes at the limit and fails one over without unbounded state. |

### 12.4 Reconnect and endpoint candidate verification

These rows exercise the frozen interface and trust invariant with injected candidate sequences. They do not select or certify a transport. The separate design gate must add its own real transport tests.

| ID | Required assertion |
| --- | --- |
| `RECON-01 last-endpoint` | Saved endpoint + approved transport-security check + fresh mutual challenge succeeds without invoking enumeration, QR, SAS, or confirmation. |
| `RECON-02 endpoint-changed` | Old DHCP address failure preserves durable binding/endpoint history, never reports SAFE, and either invokes an approved enumerator or returns `ENDPOINT_ENUMERATION_TBD`. |
| `RECON-03 candidate-not-trust` | A candidate alone—including one on the local subnet or from the last transport observation—never changes endpoint, token, or binding state. |
| `RECON-04 wrong-identity` | Candidate with wrong UUID, desktop key/fingerprint, transport-security verifier, signature, challenge context, expiry, or revoked binding is rejected and stored endpoint is unchanged. |
| `RECON-05 verified-update` | Only the approved transport-security check plus a fresh endpoint-bound mutual challenge against the saved identity atomically creates `VerifiedEndpoint` and updates the endpoint. |
| `RECON-06 no-repeat-ceremony` | A verified reconnect after endpoint change never invokes scanner, ticket, SAS, or manual-confirm code. |
| `RECON-07 interface-bounds` | Injected enumerator timeout, cancellation, out-of-subnet result, 17th concurrent operation, or 65th candidate leaves state unchanged and cannot start background work. |
| `RECON-08 transport-disabled` | With no approved enumerator, reconnect fails truthfully as `ENDPOINT_ENUMERATION_TBD`; it does not fall back to manual IP, a generic device list, Internet, or cloud. |
| `RECON-09 transport-conformance` | Every later transport implementation runs this shared verifier suite plus its separately approved wire/permission/firewall/replay/cleanup tests. |

### 12.5 Projections and Android UI truthfulness

| ID | Required assertion |
| --- | --- |
| `PROJ-01 real-data` | Each route maps only verified, allowlisted desktop data and retains evidence/reason semantics. |
| `PROJ-02 empty` | Authoritatively empty source renders `EMPTY`, not UNKNOWN, SAFE, demo data, or an error. |
| `PROJ-03 unknown` | Unestablished facts render `UNKNOWN`; environment samples are never substituted. |
| `PROJ-04 degraded` | Ledger/database/snapshot/partial failures render `DEGRADED` with no optimistic authority or raw exception. |
| `PROJ-05 unreachable` | Transport/auth failure becomes Android-local `UNREACHABLE`, never SAFE and never a fake server response. |
| `PROJ-06 recovery-truth` | Checkpoint existence, R1/R2/R3, test restore, and trusted baseline are displayed as separate facts; `R3` alone does not render TRUSTED/recoverable. |
| `PROJ-07 policy-ai-separation` | Deterministic policy and AI advisory remain separate; AI cannot upgrade policy and no provider secret/config reaches Android. |
| `PROJ-08 read-only` | Gateway route enumeration and Android client API contain no POST/PUT/PATCH/DELETE product mutation, approve/reject, checkpoint, restore, recovery, or authority action. |
| `PROJ-09 leakage-and-caps` | Paths, commands, diffs, secrets, action refs, raw rows/payloads, and over-cap items never appear. |
| `PROJ-10 no-fake-production-data` | Production build/screens fail tests if hardcoded Claude/Node/Python/Docker/Tailscale or other demo facts appear without a repository fixture. |

### 12.6 End-to-end gate

The final packaging/E2E gate uses a real Windows desktop installation, real physical/private adapter or controlled Windows network, any actual scoped firewall rule required by the reviewed gateway transport, the actual `8787` Core and separate `8788` gateway, and an Android emulator/device with Keystore and scanner input. It proves the later-approved transport-security profile, first pair, process/app restart, last-known-endpoint challenge reconnect, real projections, cleanup, and revocation. If—and only if—the separate enumeration gate has approved and implemented a transport, the E2E also proves DHCP endpoint change through that real transport. Hand-built routers, precomputed SAS from server internals, cleartext emulator-only traffic, or mocked adapter facts are not substitutes for the behaviors claimed.

## 13. Explicit v1 non-goals

The following are rejected for v1, not merely postponed within an implementation commit:

- mDNS/Android NSD, generic “find computers,” device-directory UX, or manual-IP production discovery; any permitted endpoint enumeration remains targeted to the saved bound identity under section 8;
- manual IP address as the normal reconnect UX;
- WebSocket streaming, event streaming, background permanent Android connection/scanner, push, or polling daemon;
- multiple Android devices;
- cloud, relay, Internet access, remote account, or NAT traversal;
- complex RBAC; the sole mobile permission is `read`;
- Android approve-once, reject mutation, checkpoint creation, restore, recovery mutation, arbitrary transaction, authority mutation, or any other product write;
- Android API keys, AI provider configuration, or direct AI-provider requests;
- a generic firewall/network manager, plugin architecture, or independent security-audit framework;
- transparent Core proxying, broad Core remote mode, wildcard listener, wildcard firewall scope, broad CORS, or broad CSP;
- changes to Device Link unrelated areas including K3 presentation, i18n, P7/P8 semantics, Recovery semantics, Evidence semantics, deterministic policy authority, or AI authority.

## 14. Future implementation file ownership

This map prevents overlapping workers from editing the same boundary without coordination. Exact new filenames may be adjusted within the named owner, but responsibilities may not be redistributed into K3 or unrelated R4 modules.

| Owner | Existing/new files | Responsibility |
| --- | --- | --- |
| Protocol/contracts | `agentguard/device_link/crypto.py`, `pairing.py`, new `contracts.py`; Android `network/QrPayload.kt` and new matching codec/vector tests | Canonical QR, framed transcripts, errors, ticket/FSM, cross-language vectors. No listeners. |
| Durable identity/auth | `agentguard/device_link/gateway.py` or narrow replacements; new `identity_store.py`; scoped `agentguard/storage` migration; Android `security/DeviceIdentity.kt`, `data/BoundDesktopStore.kt` | Stable identities, single binding, challenge lifecycle, read sessions, revocation. |
| LAN Sync | new platform-neutral `agentguard/device_link/lan.py` and narrow Windows backend `agentguard/device_link/lan_windows.py`; `desktop/src-tauri/src/main.rs`/`sidecar.rs` remain process-lifecycle owners only | Exact interface facts and any owned rule required by the reviewed service transport; Python gateway owns the later-approved gateway listener and coordinates resync/rollback. Enumeration networking is outside this owner until its gate. |
| Gateway boundary | new `agentguard/device_link/server.py`, `projections.py`, and transport-neutral `endpoint_candidates.py` interface; `agentguard/api/server.py` only to remove current remote mount/add local coordinator; `agentguard/cli.py` only to enforce listener separation | Separate scoped gateway service on 8788 after bounded transport review, logical-operation allowlist, endpoint verifier/interface, internal projection adapter, and Core isolation. If HTTP/ASGI is selected, implement it as a separate app. No enumeration transport implementation. |
| Android transport/persistence | `network/DeviceLinkClient.kt`, new approved-transport/auth/repository and `BoundEndpointCandidateEnumerator` interface files, Manifest/network security config | Native client, Keystore, strict errors, session handling, saved-endpoint verification, and transport-neutral candidate boundary; no enumeration implementation or product mutations. |
| Android pairing UI | New scanner/pairing/SAS screens and ViewModels in the existing Android module | Camera scan, one first-pair flow, truthful states. Coordinate around K3; do not overwrite its presentation shell. |
| Read projections/UI wiring | `R4ReadProjectionService` only through narrow additions if required; gateway projection adapter; Android repositories/screen state | Real read-only data and truthful EMPTY/UNKNOWN/DEGRADED/UNREACHABLE rendering; remove fake defaults. |
| Tests | Python Device Link tests, Windows listener/firewall integration, Android JVM/instrumented tests, final E2E workflow | The matrix in section 12, with real boundary tests where required. |

If Windows firewall ownership or network-change implementation needs privileges not available to the unelevated Tauri process, the LAN owner must resolve that with an install-time/product-owned mechanism and prove cleanup. It must not silently use a broad pre-created rule, shell out to an unspecified external script, or move network policy into Android.

## 15. Recommended atomic implementation sequence after R4 close

Each commit begins with failing tests for its own boundary and stays independently reviewable.

1. **D1 — canonical contracts and vectors.** Add strict Python/Kotlin QR codecs, error/state enums, document the existing P-256/SAS antecedent and frozen invariants, run the bounded cryptographic review, and add approved cross-language transcript vectors plus transport-neutral candidate interfaces. No new SAS derivation, production listener, or enumeration wire format.
2. **D2 — durable identities, one-time ticket, and binding transaction.** Add protected P-256 identities, single-device store, corrected pairing FSM, explicit user-mediated confirmation boundary with placement TBD, ticket replay/expiry, and atomic/idempotent/fail-closed/no-split-success tests. Keep networking loopback/test-harness only.
3. **D3 — LAN Sync adapter and Windows proof.** Implement only the section 7 fact/rule adapter and Windows tests, using short-lived test sockets to prove exact-address compatibility. Do not create persistent production listeners or mount gateway routes yet.
4. **D4 — separate gateway wiring after transport-security review.** Create the isolated scoped `8788` gateway using the separately approved profile, pairing operations, local Core coordinator bridge, strict limits/errors, and negative interface/listener tests. Remove the current Device Link mount from the Core app; never enable remote Core.
5. **D5 — Android real first-pair flow.** Add Keystore identity, camera scanner, strict QR validation, the separately approved transport-security client, independently derived SAS display, one explicit user-mediated confirmation with UI/API placement TBD, and atomic binding persistence with no split-success outcome.
6. **D6 — durable challenge reconnect.** Implement mutual canonical challenge auth, transient bearer tokens, restart behavior, local revocation enforcement, and Android repository reconnect without repeated ceremony.
7. **D7 — separate bounded endpoint-enumeration design gate, then separately approved implementation.** First select and threat-model a transport outside this contract and bind it to section 8's interface/tests. No transport code, permission, listener, or firewall rule may land until that gate is approved; any later implementation is its own atomic commit.
8. **D8 — real read projections and UI state wiring.** Add the bounded gateway DTO adapter and Android repositories/screens; delete production demo defaults; preserve deterministic policy/recovery/AI semantics and omit actions.
9. **D9 — real Windows/Android E2E and packaging gate.** Exercise install, first pair, restart, last-endpoint reconnect, real read data/state failures, revocation, process/firewall cleanup, and uninstall using actual binaries/sockets. Add DHCP-change enumeration proof only after D7's separate gate and implementation exist.

Do not start D1 before R4 closes. Do not merge K3 opportunistically into protocol or backend commits. If K3 becomes available, reconcile only presentation-owned files after its exact SHA is verified.

## 16. Acceptance summary and non-claims

This contract establishes an implementation boundary and test plan. It confirms reusable P-256/SAS/FSM and R4 read-projection primitives, but it also confirms that production LAN exposure, one-time QR authorization, durable trust, Android security/persistence, and real mobile projections are not currently implemented. Endpoint candidate enumeration transport is intentionally TBD at a separate bounded design gate and is not a missing deliverable of this contract.

This document does not claim Device Link implemented, R4 complete, L4/L5/L6 pass, P9 pass, Product Integration started, or product readiness. The next action is **WAIT FOR R4 CLOSE**.
