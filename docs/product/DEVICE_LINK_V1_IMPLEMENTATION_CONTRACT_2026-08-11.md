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
- First trust uses one short-lived QR ticket, the existing P-256 identity primitive, an independently derived SAS, and one explicit desktop-local match/reject decision.
- Once successfully bound, normal reconnect uses the durable identities and never repeats QR, SAS, or manual confirmation.

Implementation must wait for R4 close. K3 is presentation-only by reported intent; it was deliberately not audited, checked out, merged, or modified here. This contract does not depend on K3 and does not authorize a merge from or edit to it.

## 2. Current code inventory at the frozen SHA

Status terms in this section describe source that was actually inspected, not intended behavior.

### 2.1 Inventory matrix

| Area | Status | Current evidence and consequence |
| --- | --- | --- |
| P-256 primitives | IMPLEMENTED | `agentguard/device_link/crypto.py` generates and validates P-256 keys, exports DER SPKI, signs/verifies ECDSA-SHA256, and computes full SHA-256 SPKI fingerprints. Reuse these primitives. |
| Secure randomness and SAS | IMPLEMENTED | The same module generates 16-byte session IDs and 32-byte tokens and derives a six-digit HMAC-SHA256 SAS from the first three digest bytes modulo 1,000,000. Preserve this derivation rule. |
| Pairing FSM/expiry | PARTIAL | `pairing.py` has bounded in-memory sessions, exact `now >= expiry` handling, terminal states, and secret clearing. It lacks a QR authorization ticket, durable identity transition, independently reproducible transcript, and desktop-local confirmation authority. |
| Pairing HTTP flow | PARTIAL / UNSAFE | `gateway.py` and `api/server.py` expose start, connect, SAS, confirm, and complete. A LAN caller could start pairing, ask the server for the SAS, send `confirm=true`, and bind its own key. This is not an acceptable first-trust ceremony. |
| Challenge authentication | PARTIAL | Device-bound one-use challenge records, Android signature verification, bearer sessions, and revoke-driven token invalidation exist in memory. The signed value is only the raw challenge, authentication is not mutual, failed signatures do not consume the challenge, and nothing survives restart. |
| Device registry | PARTIAL | Registry metadata and read permission exist, but all records are process-local dictionaries; default configuration permits multiple devices. |
| Production gateway boundary | MISSING | `/device/v1` is mounted into the same FastAPI app as Core. There is no separate app/listener, no `8788` desktop listener, no route allowlist boundary, and no gateway-to-projection adapter. |
| Core listener boundary | PARTIAL | Packaged desktop starts Core on `127.0.0.1:8787`, but CLI `serve --allow-remote` can expose the complete Core app on `0.0.0.0:8787`. Device Link v1 must not use that path. |
| TLS and pinning | MISSING | Current Android transport is cleartext HTTP and only permits the emulator host in its network-security config. Desktop has no Device Link TLS listener or persistent TLS identity. |
| Stable desktop identity | MISSING | `create_app()` generates a new desktop UUID and private key on each creation. There is no protected durable identity store. |
| QR payload | PARTIAL | Android `QrPayload.kt` splits a permissive URI into host, port, UUID, fingerprint, session, and expiry. It has no protocol version or ticket, and does not enforce canonical form, expiry, exact IPv4, fingerprint length, duplicates, or unknown fields. |
| Android scanner/pair UI | MISSING | Manifest has no camera permission and the active Compose application has no scanner, pairing repository, ViewModel, or SAS screen. |
| Android durable identity | MISSING | No Android Keystore production key, DataStore/Room binding, persistent endpoint, restart auth, TLS pin, or revocation handling exists. The only EC key is transient test code. |
| Android screens | UI-ONLY / MOCK | The five-screen shell is not wired to `DeviceLinkClient`. Environment values are hardcoded samples; Home is default state; Changes/checkpoints are empty; AI says not configured. Fake production data must be removed, not preserved as fallback. |
| Status/checkpoints | MOCK/NOOP | Production status exposes only minimal process-memory counts. Checkpoints always returns an empty list. |
| Environment/changes/AI routes | MISSING | Android declares calls, but production does not mount those routes. Because the Core SPA catch-all excludes only `/api`, unknown `/device/v1/...` GETs can return HTML 200; a separate gateway must eliminate this behavior. |
| Read-side authority | IMPLEMENTED, REUSABLE INTERNALLY | `R4ReadProjectionService` already returns sanitized, fail-closed runtime, agents, supervision, and recovery projections from verified server-owned state. The gateway must adapt this service internally rather than expose raw Core schemas or proxy Core HTTP. |
| LAN Sync | MISSING | No executable adapter discovery, IPv4/prefix/subnet calculation, scoped firewall ownership, rollback, generation state, or network-change resync exists. See section 7. |
| Endpoint candidate enumeration | DEFERRED / TBD | Android has no implementation. Transport selection is intentionally assigned to a separate bounded design gate; section 8 freezes only its trust/interface/test boundary. This is not a missing contract deliverable. |

### 2.2 Tests that exist and what they do not prove

The frozen tree contains Python suites for audit, crypto, pairing, security regressions, production API, integration, and E2E, plus Android parser/client/instrumented tests. Useful coverage includes P-256 DER validation, fingerprinting, deterministic SAS, exact TTL boundaries, terminal secret clearing, identity-substitution rejection, challenge replay/device binding, bearer checks, and completion-time expiry races.

They do **not** prove the v1 boundary. Most integration/E2E tests use hand-built HTTP routers rather than the production FastAPI app. The Android JVM transport test uses an always-200 server. The instrumented test uses cleartext HTTP, obtains SAS from the server, uses a transient software EC key, and does not test scanner, Keystore, TLS pinning, challenge auth, restart, projections, or reconnect. No frozen test proves a physical-LAN-only `8788` listener, Core non-exposure, separate TLS identity, durable binding, independent SAS, Windows firewall scoping, or real projection data.

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
  HTTPS TCP <selected-physical-IPv4>:8788
  endpoint candidate enumeration: transport TBD / separate bounded gate
        ^
        | current subnet only, pinned identities
        |
Bound Android device (read-only)
```

The Core and gateway may run in the same sidecar process, but they must be separate ASGI applications with separate bound sockets and separate route tables. Sharing a process is not permission to mount `/device/v1` into Core or to proxy `/api/*` through the gateway.

### 3.1 Core requirements

- Bind only IPv4 loopback `127.0.0.1:8787` in packaged/product execution.
- Continue to be the sole local mutation and policy authority.
- Expose desktop-local pairing session creation, cancellation, status polling, and SAS match/reject through authenticated loopback routes only.
- Never expose the QR ticket, desktop-local confirm action, `/api/session`, arbitrary `/api/*`, recovery mutations, supervision mutations, AI provider configuration, or secrets on `8788`.
- Device Link implementation must not invoke or rely on the current `--allow-remote` Core mode. Product tests must fail if the Device Link path makes Core reachable from a non-loopback socket.

### 3.2 Gateway requirements

- Bind the HTTPS TCP gateway only to the adapter facts returned by LAN Sync at exact IPv4:`8788`. Candidate-enumeration sockets/transport are not selected or authorized by this contract. No wildcard bind, fallback bind, secondary interface, IPv6 listener, or gateway port selection is allowed.
- Serve TLS 1.3 HTTPS for pairing, authentication, and reads. Device Link is runtime-gated to Android API 29+ as specified in section 11.1; there is no TLS 1.2 fallback or bundled-provider ambiguity. Native Android is the only client; do not install CORS middleware and reject requests carrying a browser `Origin` header.
- Do not serve SPA files. Unknown routes, including every `/api/*`, return a typed JSON 404.
- Expose only the pairing claim/result operations, mutual challenge authentication, and the read routes defined in section 10.
- Enforce one bound device, request/body limits, per-session and per-source rate limits, exact schema versions, and stable error responses.
- Obtain data through a narrow `DeviceProjectionSource`; never forward caller paths, query strings, headers, or bodies to Core.

### 3.3 WebView, CSP, CORS, and capability boundaries

The Tauri WebView continues to call only Core loopback `127.0.0.1:8787`. It does not call the LAN gateway. Consequently Device Link does not require adding `https://<dynamic-lan-ip>:8788` to WebView CSP, does not require wildcard `connect-src`, and does not require any CORS expansion. No Tauri capability or remote-content permission is needed for Android networking. Any implementation that broadens CSP, adds wildcard CORS, changes `allow_remote`, or exposes `8787` to LAN fails this contract.

## 4. Durable identities and key separation

V1 uses distinct keys for distinct purposes:

| Identity/material | Lifetime and storage | Purpose |
| --- | --- | --- |
| Desktop UUID | Stable per desktop installation; protected durable desktop state | Selects the intended already-known desktop. It is an identifier, not a secret. |
| Desktop P-256 identity key | Stable; private key protected with Windows user-scoped facilities; public DER SPKI persisted | Signs pairing, binding receipts, and fresh auth responses. Its SHA-256 SPKI fingerprint is the durable trust anchor. |
| Gateway TLS key/certificate | Stable across DHCP changes; private key protected separately from identity key | Provides HTTPS confidentiality and a separately pinned TLS SPKI. It must not be reused as the identity key. |
| Android UUID | Stable per app installation/binding record | Identifies the single Android device. |
| Android P-256 identity key | Non-exportable Android Keystore key | Signs pairing claims, finalization, receipt recovery/acknowledgement, and auth responses. |
| QR authorization ticket | 32 random bytes, memory-only during one pairing ceremony | First-pair capability and existing HMAC-SAS secret. Never a durable trust anchor. |
| Pairing flow token | Random, session-bound, expiry-bound, temporary | Authorizes polling/finalization after the ticket has been claimed. It grants no read projection access. |
| Read session token | Random 32-byte bearer, memory-only, one hour maximum | Short-lived gateway read authentication after a successful mutual challenge. |

Desktop UUID, desktop identity key, TLS identity, the one bound-device record, and any unacknowledged signed binding receipt must survive Core/gateway restart. Pairing sessions, raw tickets, SAS values, challenges, and bearer tokens must not survive restart. Restart before the binding transaction cancels the ceremony; restart after that transaction permits receipt recovery and then challenge authentication, never a new QR.

## 5. Canonical QR contract

### 5.1 URI and canonical encoding

The only accepted URI form is:

```text
agentstate://pair/v1?protocol_version=1&host=<ipv4>&port=8788&desktop_uuid=<uuid>&desktop_identity_fingerprint=<hex64>&tls_spki_fingerprint=<hex64>&pairing_session_id=<hex32>&expires_at=<unix-seconds>&authorization_ticket=<base64url43>
```

Rules:

- UTF-8 input must decode to ASCII only and be at most 512 bytes.
- Scheme, authority, path, field names, and field order are exact and lowercase: scheme `agentstate`, authority `pair`, path `/v1`, then the nine query fields shown above.
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
| `tls_spki_fingerprint` | Exactly 64 lowercase hexadecimal characters: SHA-256 of the gateway certificate SPKI. It is separate from the desktop identity fingerprint. |
| `pairing_session_id` | Exactly 32 lowercase hexadecimal characters representing 16 CSPRNG bytes. |
| `expires_at` | Unsigned Unix UTC seconds in minimal decimal, at most 10 digits. Session lifetime is 120 seconds. Validity is `server_now < expires_at`; equality is expired. Android may reject locally using its clock but may never extend server expiry or add grace. |
| `authorization_ticket` | Exactly 43 base64url characters without padding, decoding to 32 CSPRNG bytes (256 bits). Standard base64, padding, and non-canonical encodings are rejected. |

The QR is a user-mediated bootstrap capability. It is not encrypted; adding ad-hoc encryption would not hide it from a camera that can read the ticket and would create a second key-distribution problem. Confidentiality and server authentication begin with the pinned HTTPS connection.

### 5.3 Ticket lifecycle

The raw 32-byte ticket is also the `pairing_secret` input to the existing `derive_sas()` HMAC. The server keeps it only in the in-memory pairing session, compares submitted ticket bytes in constant time, and clears them on every terminal outcome. It is never written to the durable device registry, logs, Evidence payloads, analytics, crash reports, or Android durable binding state.

| Ticket/session state | Meaning | Allowed next state |
| --- | --- | --- |
| `ISSUED` | Created by an authenticated desktop-local action; QR visible; no Android identity trusted. | `CLAIMED`, `REJECTED`, `EXPIRED`, `CANCELLED` |
| `CLAIMED` | First valid ticket redemption plus Android P-256 possession proof accepted. Ticket is immediately non-redeemable; a temporary flow token replaces it. | `SAS_PENDING`, `REJECTED`, `EXPIRED`, `FAILED` |
| `SAS_PENDING` | Both peers have independently derived/displayed the same transcript-bound SAS. | `LOCALLY_CONFIRMED`, `REJECTED`, `EXPIRED`, `FAILED` |
| `LOCALLY_CONFIRMED` | Desktop user made the single explicit match decision through Core loopback. This is still not durable trust. | `BOUND`, `EXPIRED`, `FAILED` |
| `BOUND` / ticket `CONSUMED` | An atomic transaction stored the one read-only Android binding and consumed/cleared the ticket. Durable device identity begins here. | normal challenge auth; local revocation |
| `REJECTED` | Desktop user selected reject or either peer explicitly cancelled after claim. | terminal; ticket invalid |
| `EXPIRED` | `now >= expires_at` at any transition. | terminal; ticket invalid |
| `FAILED` / `CANCELLED` | Validation, persistence, restart, capacity, or local cancellation ended the ceremony. | terminal; ticket invalid |

Only one claim can win. Concurrent, reordered, duplicate, wrong-session, or replayed tickets must produce stable failure codes and cannot advance state. A claim for another session is not treated as a claim for the QR session. Reject, expiry, cancellation, or failure immediately invalidates the ticket and clears both the raw ticket and derived SAS material. Successful binding consumes the ticket in the same transaction that commits the device record; failure to commit leaves no binding and no success receipt.

## 6. Pairing protocol and durable-trust transition

### 6.1 Local start

An authenticated desktop WebView asks Core loopback to create a pairing session. Core refuses if a device is already bound, LAN exposure is unavailable/degraded, or a non-terminal pairing already exists. Core obtains the current LAN endpoint, creates the 120-second ticket/session, and renders the canonical QR. There is no unauthenticated `pair/start` on `8788`.

### 6.2 Android claim and key proof

After strict local QR validation, Android:

1. Opens HTTPS to the QR host/port and requires the peer TLS SPKI to match `tls_spki_fingerprint` before sending the ticket.
2. Receives the full desktop identity DER SPKI and requires its SHA-256 fingerprint to match `desktop_identity_fingerprint`.
3. Sends protocol version, session ID, raw ticket, Android UUID, Android identity DER SPKI, a 32-byte Android nonce, a bounded display name, and an ECDSA signature proving possession of that submitted key over a domain-separated claim transcript.
4. Receives a 32-byte desktop nonce, the canonical SAS transcript facts, a temporary flow token, and a desktop P-256 signature over the response. Android verifies the signature with the QR-pinned desktop identity.

The claim is accepted only for the QR session, exact endpoint, exact expiry, unused ticket, and supported protocol. The flow token is random, bound to the session and submitted Android key, expires with the pairing session, and cannot call read routes.

### 6.3 Canonical SAS transcript

Both implementations construct exactly these bytes in this order:

```text
ASCII("AgentStateGuard/DeviceLink/SAS/v1\0")
u16be(protocol_version)
raw16(pairing_session_id)
raw16(desktop_uuid RFC-4122 network order)
raw16(android_uuid RFC-4122 network order)
u16be(len(desktop_identity_spki_der)) || desktop_identity_spki_der
u16be(len(android_identity_spki_der)) || android_identity_spki_der
raw32(tls_spki_fingerprint)
raw32(desktop_nonce)
raw32(android_nonce)
u64be(expires_at Unix seconds)
raw4(host IPv4 network order)
u16be(port)
```

SPKI DER is limited to 256 bytes. Every fixed-length decode and UUID conversion must be exact. No UTF-8 concatenation, JSON serialization, local monotonic time, short fingerprint, implicit field, or platform-native integer order is permitted.

SAS derivation preserves the frozen implementation rule:

```text
digest = HMAC-SHA256(key=authorization_ticket_bytes, message=canonical_transcript)
number = unsigned_big_endian(digest[0:3]) mod 1_000_000
sas = six-digit zero-padded decimal, displayed as "DDD DDD"
```

This transcript framing is a necessary correction, not a new cryptosystem: the frozen transcript ambiguously concatenates variable-length values, uses a process-monotonic expiry another device cannot reproduce, and disagrees with its published fingerprint encoding. A fixed Python/Kotlin golden vector with a literal expected SAS is a D1 gate.

### 6.4 Single explicit confirmation and binding

Android displays its locally derived SAS. Core displays its locally derived SAS in the desktop WebView. The user performs exactly one explicit action on the desktop: `MATCH` or `REJECT`. The confirmation route exists only on authenticated `127.0.0.1:8787`; Android and LAN callers cannot invoke it.

`MATCH` records local confirmation but does not let the desktop invent an Android acknowledgment. Android finalizes with the pairing flow token and a P-256 signature bound to the transcript. The server then atomically:

- rechecks expiry, exact state, ticket claim, local confirmation, and Android key proof;
- requires that no other device is bound;
- persists the Android UUID, full DER SPKI/fingerprint, permission `read`, protocol version, and binding timestamp;
- records the successful security event without ticket/SAS/token material;
- marks the ticket `CONSUMED` and clears ephemeral secrets; and
- creates a desktop-signed binding receipt and short-lived read session token.

The binding transaction also durably stores the exact signed receipt bytes, session ID, Android identity fingerprint, and `android_acknowledged_at=null`. This small recovery record is keyed to the new binding and survives restart. Before sending finalize, Android atomically marks its non-authoritative pending record `FINALIZE_UNCERTAIN`; that record contains only session ID, flow token, expiry, Android key alias/UUID, desktop UUID/public key/fingerprints, and QR endpoint, never the raw ticket, SAS, or pairing secret. Android verifies the receipt and atomically promotes this record to the active binding, then sends the signed acknowledgement.

If response generation, delivery, Android persistence, or either process crashes after the desktop commit, Android calls the receipt-recovery route using the pending identity and a fresh possession proof. The desktop returns the exact stored signed receipt. Receipt recovery remains available for that same bound key until acknowledgement or local revocation and does not require the expired flow token. On valid acknowledgement the desktop records `android_acknowledged_at`, retains the receipt digest/security event with the binding, and may delete the full recovery payload after 24 hours. Repeating finalize or recovery for the same session/key is idempotent and can never bind a second identity. After the Android record is active, all future sessions use challenge auth; QR and SAS are invalid operations unless the desktop locally revokes/resets the sole binding first.

### 6.5 Exact HTTP surface, encodings, and signed messages

#### Common wire rules

Pairing and auth HTTP requests use TLS 1.3 and exact `Content-Type: application/json`. JSON object key order is irrelevant, but duplicate keys, unknown keys, missing keys, non-canonical base64url/hex, JSON numbers outside the declared integer type, and trailing content are rejected. Binary JSON values use unpadded base64url and must decode and re-encode identically. DER SPKI is at most 256 bytes; DER ECDSA signatures are at most 80 bytes; nonces/tickets/tokens are exactly 32 bytes; session/challenge IDs are exactly 16 bytes. UUID and IPv4 encoding matches section 6.3.

The binary signed encodings below use:

```text
B16(bytes) = u16be(length) || bytes
U(uuid)    = raw RFC-4122 16-byte network-order UUID
FP(hex64)  = raw 32 bytes decoded from lowercase hex
H(bytes)   = SHA-256(bytes), raw 32 bytes
```

A domain literal includes its final NUL byte. Signatures are DER ECDSA P-256/SHA-256 over the exact preimage bytes and are never included in their own preimage. Every listed field is required; no implementation-defined field is signed implicitly.

#### Route table

Desktop-local Core routes are protected by the existing Core session authentication and exist only on `127.0.0.1:8787`:

| Method and path | Exact purpose |
| --- | --- |
| `POST /api/device-link/v1/pairing-sessions` | Exact empty JSON object; create one session and return canonical QR plus state/expiry. |
| `GET /api/device-link/v1/pairing-sessions/{session_id}` | Return state and local SAS only to the authenticated desktop UI. |
| `POST /api/device-link/v1/pairing-sessions/{session_id}/decision` | Exact body `{"decision":"MATCH"}` or `{"decision":"REJECT"}`; the sole manual SAS decision. |
| `DELETE /api/device-link/v1/pairing-sessions/{session_id}` | Desktop-local cancel; idempotently invalidates ticket/flow. |

Gateway pairing/auth routes on exact physical IPv4 `:8788` are:

| Method and path | Exact request/response field set |
| --- | --- |
| `POST /device/v1/pair/claim` | Request: `schema_version="device-link-pair/1"`, protocol version, session ID, ticket, Android UUID/SPKI/nonce/display name, and `claim_signature`. Response: session ID, desktop SPKI/nonce, expiry, 32-byte `pairing_flow_token`, and `claim_response_signature`. |
| `GET /device/v1/pair/{session_id}/state` | `Authorization: Pairing <base64url43-flow-token>`; returns only `CLAIMED|SAS_PENDING|LOCALLY_CONFIRMED|BOUND|REJECTED|EXPIRED|FAILED` and expiry. It never returns SAS or ticket material. |
| `POST /device/v1/pair/{session_id}/finalize` | Pairing authorization plus exact `schema_version`, session ID, and `finalize_signature`; returns the signed receipt and transient read token, idempotently for the same committed key. |
| `POST /device/v1/pair/receipt/recover` | Request: schema/protocol, session ID, desktop/Android UUIDs, fresh nonce/expiry, and `recovery_signature`; returns the stored receipt/signature and no durable secret. No flow token is required after a committed binding. |
| `POST /device/v1/pair/receipt/ack` | Request: schema/protocol, session ID, receipt hash, and `ack_signature`; records Android receipt persistence. |
| `POST /device/v1/auth/challenge` | Exact body with schema/protocol and bound Android UUID; returns challenge fields plus desktop signature. |
| `POST /device/v1/auth/response` | Exact body with challenge ID, Android UUID, and Android signature; returns read token/expiry/generation plus desktop signature. |

There is no Android reject route. A LAN caller cannot create a session or make the SAS decision. Pairing authorization is accepted only as `Pairing`; read routes accept only `Bearer`; these scopes are not interchangeable.
#### Exact JSON field encodings

All examples below show the complete allowed key set; angle-bracket values denote the declared type, not optional text. JSON key order is illustrative. Fields use:

| Field family | JSON/path encoding |
| --- | --- |
| `pairing_session_id`, path `{session_id}`, `challenge_id` | Exactly 32 lowercase hex characters representing raw 16 bytes. They are never base64url. |
| `receipt_hash` | Exactly 64 lowercase hex characters: SHA-256 of canonical `BINDING_RECEIPT` bytes. |
| Every `*_uuid` | Canonical lowercase RFC 4122 string, 36 characters. |
| Every `*_fingerprint` | Exactly 64 lowercase hex characters representing raw SHA-256 bytes. |
| `authorization_ticket`, `pairing_flow_token`, `read_session_token`, every `*_nonce` | Canonical unpadded base64url of exactly 32 bytes: 43 characters. |
| Every `*_spki` | Canonical unpadded base64url of validated DER P-256 SPKI, decoded length 1–256 bytes. |
| Every `*_signature` | Canonical unpadded base64url of validated DER P-256 ECDSA signature, decoded length 1–80 bytes. |
| `issued_at`, `expires_at`, `bound_at` | Non-negative JSON integer containing Unix UTC seconds; never a string or floating value. |
| `gateway_generation` | Non-negative JSON integer, diagnostic only. |
| `host`, `server_host`, `candidate_host` | Canonical dotted-decimal IPv4 string defined in section 5.2. |
| `port`, `server_port` | JSON integer exactly `8788`. |
| `protocol_version` | JSON integer exactly `1`. |
| `permission` | Exact string `READ`. |
| Pairing header | Exact `Authorization: Pairing <43-character pairing_flow_token>`. |
| Read header | Exact `Authorization: Bearer <43-character read_session_token>`. |
| Core header | Existing exact `X-Session-Token: <Core token>`; it is never accepted by the gateway. |

#### Exact Core-local JSON

`POST /api/device-link/v1/pairing-sessions` request is exactly `{}`; HTTP 201 response is:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "ISSUED",
  "expires_at": 1780000000,
  "qr_uri": "<canonical URI from section 5>"
}
```

`GET /api/device-link/v1/pairing-sessions/{session_id}` HTTP 200 response is:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "<ISSUED|CLAIMED|SAS_PENDING|LOCALLY_CONFIRMED|BOUND|REJECTED|EXPIRED|FAILED|CANCELLED>",
  "expires_at": 1780000000,
  "sas": "<DDD DDD or null>"
}
```

`sas` is non-null only in `SAS_PENDING` or `LOCALLY_CONFIRMED`. Decision request is exactly `{"decision":"MATCH"}` or `{"decision":"REJECT"}`; response is exactly:

```json
{
  "schema_version": "device-link-pair/1",
  "pairing_session_id": "<hex32>",
  "state": "<LOCALLY_CONFIRMED|REJECTED>"
}
```

Successful cancel returns HTTP 200 with the same three keys and `state="CANCELLED"`.

#### Exact gateway pairing JSON

`POST /device/v1/pair/claim` request:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "authorization_ticket": "<base64url43>",
  "android_uuid": "<uuid>",
  "android_identity_spki": "<base64url DER>",
  "android_nonce": "<base64url43>",
  "display_name": "<bounded NFC text>",
  "claim_signature": "<base64url DER signature>"
}
```

HTTP 200 claim response:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "CLAIMED",
  "desktop_uuid": "<uuid>",
  "desktop_identity_spki": "<base64url DER>",
  "desktop_nonce": "<base64url43>",
  "expires_at": 1780000000,
  "pairing_flow_token": "<base64url43>",
  "claim_response_signature": "<base64url DER signature>"
}
```

Authenticated pair-state response:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "<CLAIMED|SAS_PENDING|LOCALLY_CONFIRMED|BOUND|REJECTED|EXPIRED|FAILED>",
  "expires_at": 1780000000
}
```

Finalize request:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "finalize_signature": "<base64url DER signature>"
}
```

The nested receipt object used by finalize/recover is exactly:

```json
{
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "desktop_uuid": "<uuid>",
  "android_uuid": "<uuid>",
  "desktop_identity_fingerprint": "<hex64>",
  "android_identity_fingerprint": "<hex64>",
  "tls_spki_fingerprint": "<hex64>",
  "permission": "READ",
  "bound_at": 1780000000
}
```

Finalize HTTP 200 response:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "BOUND",
  "binding_receipt": {
    "protocol_version": 1,
    "pairing_session_id": "<hex32>",
    "desktop_uuid": "<uuid>",
    "android_uuid": "<uuid>",
    "desktop_identity_fingerprint": "<hex64>",
    "android_identity_fingerprint": "<hex64>",
    "tls_spki_fingerprint": "<hex64>",
    "permission": "READ",
    "bound_at": 1780000000
  },
  "receipt_signature": "<base64url DER signature>",
  "read_session_token": "<base64url43>",
  "session_token_expires_at": 1780003600,
  "gateway_generation": 7
}
```

Receipt recovery request:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "desktop_uuid": "<uuid>",
  "android_uuid": "<uuid>",
  "recovery_nonce": "<base64url43>",
  "candidate_host": "192.168.1.23",
  "port": 8788,
  "tls_spki_fingerprint": "<hex64>",
  "expires_at": 1780000060,
  "recovery_signature": "<base64url DER signature>"
}
```

Receipt recovery HTTP 200 response contains no session token:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "state": "BOUND",
  "binding_receipt": {
    "protocol_version": 1,
    "pairing_session_id": "<hex32>",
    "desktop_uuid": "<uuid>",
    "android_uuid": "<uuid>",
    "desktop_identity_fingerprint": "<hex64>",
    "android_identity_fingerprint": "<hex64>",
    "tls_spki_fingerprint": "<hex64>",
    "permission": "READ",
    "bound_at": 1780000000
  },
  "receipt_signature": "<base64url DER signature>"
}
```

Receipt acknowledgement request and response:

```json
{
  "schema_version": "device-link-pair/1",
  "protocol_version": 1,
  "pairing_session_id": "<hex32>",
  "receipt_hash": "<hex64>",
  "ack_signature": "<base64url DER signature>"
}
```

```json
{
  "schema_version": "device-link-pair/1",
  "pairing_session_id": "<hex32>",
  "acknowledged": true
}
```

#### Exact gateway authentication JSON

Challenge request:

```json
{
  "schema_version": "device-link-auth/1",
  "protocol_version": 1,
  "android_uuid": "<uuid>"
}
```

Challenge HTTP 200 response:

```json
{
  "schema_version": "device-link-auth/1",
  "protocol_version": 1,
  "challenge_id": "<hex32>",
  "challenge_nonce": "<base64url43>",
  "desktop_uuid": "<uuid>",
  "android_uuid": "<uuid>",
  "server_host": "192.168.1.23",
  "server_port": 8788,
  "tls_spki_fingerprint": "<hex64>",
  "issued_at": 1780000000,
  "expires_at": 1780000300,
  "desktop_signature": "<base64url DER signature>"
}
```

Auth response request:

```json
{
  "schema_version": "device-link-auth/1",
  "protocol_version": 1,
  "challenge_id": "<hex32>",
  "android_uuid": "<uuid>",
  "android_signature": "<base64url DER signature>"
}
```

Auth response HTTP 200:

```json
{
  "schema_version": "device-link-auth/1",
  "protocol_version": 1,
  "challenge_id": "<hex32>",
  "desktop_uuid": "<uuid>",
  "android_uuid": "<uuid>",
  "read_session_token": "<base64url43>",
  "issued_at": 1780000000,
  "expires_at": 1780003600,
  "gateway_generation": 7,
  "desktop_signature": "<base64url DER signature>"
}
```

All pairing/auth errors use exactly `{"schema_version":"device-link-error/1","code":"<stable-code>","retryable":false}`; rate-limit errors set `retryable=true` and HTTP `Retry-After`. There is no message/detail/stack field. Projection success schemas remain section 10's envelope.



#### Claim proof and response

The claim display name is NFC-normalized, 1–64 Unicode scalar values, at most 128 UTF-8 bytes, with control, bidi-control, line-break, and NUL characters rejected. It is display metadata, never identity.

Android signs:

```text
CLAIM =
  ASCII("AgentStateGuard/DeviceLink/CLAIM/v1\0") ||
  u16be(protocol_version) ||
  raw16(pairing_session_id) ||
  U(desktop_uuid) || U(android_uuid) ||
  B16(android_identity_spki_der) ||
  raw32(android_nonce) ||
  u64be(expires_at) ||
  raw4(host) || u16be(port) ||
  FP(desktop_identity_fingerprint) ||
  FP(tls_spki_fingerprint) ||
  H(authorization_ticket_bytes) ||
  B16(NFC_UTF8(display_name))
```

After constant-time ticket validation and Android-key verification, Desktop signs:

```text
CLAIM_RESPONSE =
  ASCII("AgentStateGuard/DeviceLink/CLAIM-RESPONSE/v1\0") ||
  H(CLAIM) ||
  B16(desktop_identity_spki_der) ||
  raw32(desktop_nonce) ||
  H(pairing_flow_token_bytes) ||
  u64be(expires_at)
```

The JSON response carries the raw flow token only inside pinned TLS. Android verifies the desktop SPKI fingerprint and `CLAIM_RESPONSE` before entering `SAS_PENDING`.

#### Finalize and durable receipt

After desktop-local `MATCH`, Android signs:

```text
FINALIZE =
  ASCII("AgentStateGuard/DeviceLink/FINALIZE/v1\0") ||
  u16be(protocol_version) ||
  raw16(pairing_session_id) ||
  U(desktop_uuid) || U(android_uuid) ||
  H(CLAIM) ||
  H(canonical_sas_transcript) ||
  H(pairing_flow_token_bytes)
```

The atomic binding transaction produces and Desktop signs:

```text
BINDING_RECEIPT =
  ASCII("AgentStateGuard/DeviceLink/BINDING-RECEIPT/v1\0") ||
  u16be(protocol_version) ||
  raw16(pairing_session_id) ||
  U(desktop_uuid) || U(android_uuid) ||
  FP(desktop_identity_fingerprint) ||
  FP(android_identity_fingerprint) ||
  FP(tls_spki_fingerprint) ||
  u8(permission = 1 /* READ */) ||
  u64be(bound_at)
```

The receipt JSON contains exactly those facts, `receipt_signature`, read token, token expiry, and gateway generation. The token and generation are not durable authority and are not part of the receipt.

For post-commit crash recovery, Android signs a request with a fresh 32-byte nonce and expiry at most 60 seconds ahead:

```text
RECEIPT_RECOVER =
  ASCII("AgentStateGuard/DeviceLink/RECEIPT-RECOVER/v1\0") ||
  u16be(protocol_version) ||
  raw16(pairing_session_id) ||
  U(desktop_uuid) || U(android_uuid) ||
  raw32(recovery_nonce) ||
  raw4(candidate_host) || u16be(port) ||
  FP(tls_spki_fingerprint) ||
  u64be(expires_at)
```

The gateway verifies it with the newly bound Android key and returns the exact stored `BINDING_RECEIPT` plus its signature. After Android persists the active record it signs:

```text
RECEIPT_ACK =
  ASCII("AgentStateGuard/DeviceLink/RECEIPT-ACK/v1\0") ||
  u16be(protocol_version) ||
  raw16(pairing_session_id) ||
  U(desktop_uuid) || U(android_uuid) ||
  H(BINDING_RECEIPT)
```

Receipt recovery is bound to the saved pins and Android key; it is not a replacement pairing path.

#### Challenge and auth response

A challenge ID is 16 CSPRNG bytes. Desktop creates and signs:

```text
AUTH_CHALLENGE =
  ASCII("AgentStateGuard/DeviceLink/AUTH-CHALLENGE/v1\0") ||
  u16be(protocol_version) ||
  raw16(challenge_id) ||
  raw32(challenge_nonce) ||
  U(desktop_uuid) || U(android_uuid) ||
  raw4(server_host) || u16be(server_port) ||
  FP(tls_spki_fingerprint) ||
  u64be(issued_at) || u64be(expires_at)
```

Android first verifies that desktop signature, then signs:

```text
AUTH_REQUEST =
  ASCII("AgentStateGuard/DeviceLink/AUTH-REQUEST/v1\0") ||
  H(AUTH_CHALLENGE)
```

The server consumes the challenge before reporting signature success/failure. On success it creates a 32-byte token and Desktop signs:

```text
AUTH_RESPONSE =
  ASCII("AgentStateGuard/DeviceLink/AUTH-RESPONSE/v1\0") ||
  H(AUTH_CHALLENGE) ||
  U(desktop_uuid) || U(android_uuid) ||
  H(read_session_token_bytes) ||
  u64be(issued_at) || u64be(expires_at) ||
  u64be(gateway_generation)
```

Android accepts the token only after verifying `AUTH_RESPONSE`. The TLS fingerprint and endpoint are already transitively bound by `AUTH_CHALLENGE`.

#### Numeric limits

| Limit | Exact v1 value |
| --- | --- |
| HTTP request body | 4,096 bytes maximum; no content encoding. |
| Projection response | 256 KiB maximum after JSON serialization. |
| Pairing concurrency | One non-terminal session and one winning claim. |
| Pair claim rate | Five attempts per source IP and five per session per rolling 60 seconds. |
| Pair state/finalize rate | Two requests per second and 120 total per session. |
| Receipt recovery/ack rate | Ten per bound device per hour. |
| Outstanding auth challenges | Four for the sole device; oldest is invalidated on overflow. |
| Auth challenge/response rate | Ten per device and 20 per source IP per rolling 60 seconds. |
| Authenticated reads | 120 per device per rolling 60 seconds. |
| Server header/body read timeout | Five seconds. |
| Android connect/read timeout | Three/five seconds. |
| Pairing/challenge/token TTL | 120 seconds / at most 300 seconds / at most 3,600 seconds. |

Boundary-value and one-over-limit tests are mandatory. Rate-limit state is bounded memory, keyed without logging raw IP/device/ticket/token values, and resets safely on restart; restart never restores a consumed ticket or revoked binding.


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

The Python sidecar gateway runtime is the sole owner of the production HTTPS TCP socket. The narrow Windows LAN adapter supplies verified interface facts and owns only the exact firewall policy/rule handle; it neither accepts nor forwards application bytes. The inbound rule is limited to the exact sidecar executable, exact local IPv4, TCP port 8788, current remote subnet CIDR, and Private profile. No rule may use Any profile, Any program, Any local address, Any remote address, a port range, or `0.0.0.0`. The adapter removes or replaces only rules whose exact owned IDs are in its durable state; it never edits unrelated rules. Any future enumeration rule/socket belongs to the separate design gate and is not authorized here.

On network change, the Python runtime first marks the old exposure unavailable and closes its old HTTPS socket. The adapter resolves a fresh complete fact set and applies/verifies the new owned TCP rule; the Python runtime binds the exact socket and reports its actual local address back for adapter verification before publishing the new exposure/generation. Superseded owned rules are then removed. Any error leaves the gateway unavailable and performs bounded rollback; it must not widen a rule or wildcard-bind as fallback. `disable()` coordinates socket close and removal of only owned rules, is idempotent, and reports residual/rollback failure explicitly.


## 8. Bound-device endpoint candidate boundary

`ENDPOINT_ENUMERATION_TRANSPORT = TBD / SEPARATE BOUNDED DESIGN GATE`

This is an intentional scope decision, not a missing deliverable. This contract does not select, recommend, encode, or reject a particular probing, beacon, broadcast, multicast, unicast-scan, OS-discovery, or other candidate-enumeration transport. No worker may infer a wire algorithm from LAN subnet facts or add one in D1–D6.

### 8.1 Frozen trust invariant

Endpoint candidate enumeration establishes **reachability only**. A candidate address, source address, transport response, local-subnet location, saved last endpoint, or successful TCP/TLS connection is not trust and cannot create or renew a binding.

The durable selector supplied to any future enumerator is limited to the existing bound-desktop facts:

- saved desktop UUID;
- saved full desktop identity public key and SHA-256 fingerprint;
- saved TLS SPKI fingerprint;
- supported protocol version; and
- last-known IPv4:`8788` as a hint, not authority.

A candidate may replace the saved endpoint only after Android verifies the saved desktop identity through the pinned TLS identity and the fresh mutual cryptographic challenge in section 9. The challenge must bind the candidate IPv4:`8788`, both durable UUIDs, protocol version, TLS fingerprint, nonce, and expiry. Failure at any stage leaves the saved endpoint and binding unchanged.

### 8.2 Implementation-facing interface

The transport-neutral boundary is:

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

`BoundDesktopSelector` contains only the saved identity inputs above. `LocalNetworkScope` contains the Android active-network handle, canonical local IPv4/prefix/subnet, and invocation start time. `EndpointCandidate` contains only canonical IPv4, fixed service port 8788, opaque mechanism evidence, and observation time; it is explicitly untrusted. The enumerator cannot access the binding private key, persist an endpoint, issue a session token, create trust, trigger pairing, or return a device list. Only `EndpointVerifier` may produce a `VerifiedEndpoint`, and only the repository may atomically persist that result.

### 8.3 Bounds every later transport must satisfy

The separate design gate must choose and threat-model a transport before implementation. Whatever it chooses must satisfy all of these frozen limits:

- current Android local network/subnet only; out-of-subnet candidates are rejected before connection;
- one foreground invocation at a time, only after last-known endpoint authentication fails or the user explicitly retries;
- finite total deadline no greater than five seconds, cancellation on app background/network change, and no permanent scanner/listener on Android;
- finite concurrency no greater than 16 and at most 64 candidate objects per invocation;
- no Internet, cloud, relay, account directory, NAT traversal, or cross-subnet request;
- no generic computer/device listing and no trust based on a human-readable name or address;
- no QR, SAS, or manual confirmation during a reconnect attempt;
- no endpoint write before pinned TLS plus a fresh mutual challenge proves the saved desktop identity; and
- bounded packets/requests, replay handling, rate limiting, permissions, firewall effects, cleanup, and negative tests defined by that later gate.

The separate gate must document its OS APIs, wire format, required Android/Windows permissions, listener/firewall ownership, spoof/replay model, numeric packet/rate limits, cleanup, and real Windows/Android verification. Until that gate is approved, production candidate enumeration remains disabled; last-known endpoint challenge reconnect still works. This disabled state is `ENDPOINT_ENUMERATION_TBD`, not a Device Link trust failure and not permission to fall back to manual IP or generic discovery.

### 8.4 Transport-neutral test obligations

Before any enumeration transport is accepted, shared repository/verifier tests must prove:

- an enumerated candidate alone never changes endpoint or binding state;
- an out-of-subnet, wrong UUID, wrong desktop key/fingerprint, wrong TLS pin, expired/replayed challenge, or revoked binding is rejected;
- only the candidate that completes pinned TLS and the fresh mutual challenge becomes `VerifiedEndpoint`;
- success updates the endpoint atomically without QR, SAS, or manual confirmation;
- timeout, cancellation, over-concurrency, and over-candidate limits leave state unchanged;
- last-known endpoint success bypasses enumeration entirely; and
- the same verifier tests run against every future transport implementation.

## 9. Post-bind authentication and reconnect

Normal reconnect is:

1. Try saved IPv4:`8788` with the saved TLS SPKI pin.
2. If reachable, run mutual challenge authentication.
3. If unreachable and an independently approved candidate enumerator exists, request bounded local candidates through section 8's interface and apply pinned TLS plus mutual challenge to each; otherwise report `ENDPOINT_ENUMERATION_TBD` without changing trust or endpoint state.
4. Receive an in-memory read session token. Do not show QR or SAS.

The challenge record is 32 CSPRNG bytes, bound to protocol version, challenge ID, desktop UUID, Android UUID, candidate endpoint, TLS SPKI fingerprint, and an absolute expiry no more than 300 seconds ahead. Android signs a domain-separated canonical transcript containing all of those values; Desktop verifies the bound Android key. Desktop signs the response transcript, and Android verifies its saved desktop key. A challenge is consumed on the first response attempt, including a bad signature, and is removed after terminal use. Replay, cross-device use, endpoint substitution, expired challenge, revoked device, or protocol mismatch is denied.

The resulting 32-byte random bearer token is scoped to the one device, read permission, gateway generation, and at most one hour. It is held only in Android memory and gateway memory. Restart or expiry requires another challenge, not re-pairing. Revocation is a desktop-local operation: it deletes/inactivates the durable binding and immediately invalidates all challenges and tokens. A revoked Android cannot pass candidate verification or update a saved endpoint.

## 10. Read-only gateway projection contract

### 10.1 Common envelope and truth states

Authenticated gateway routes are GET-only and use:

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
| `UNREACHABLE` | Android could not obtain an authenticated response because transport, TLS, or auth failed. This is synthesized by the Android repository and is never fabricated by a reachable gateway response. |

`UNREACHABLE != SAFE`, `EMPTY != SAFE`, and `UNKNOWN != SAFE`. `CHECKPOINT_CREATED` does not imply recoverability. Recovery level `R3` does not imply `trusted_baseline_status == TRUSTED`. The Android UI must render those facts separately and must not infer an overall green/safe status.

All arrays are capped at 100 items, sorted deterministically, and report `truncated=true` when capped. String atoms are allowlisted/bounded; no local paths, commands, raw diffs, file contents, environment variables, raw database rows, exception text, action references, tokens, tickets, provider credentials, or arbitrary ledger payload keys are returned.

### 10.2 Routes and minimum DTOs

| Route | Minimum `data` surface and source |
| --- | --- |
| `GET /device/v1/read/home` | Derived summary only: section `{projection,state,reason_code,item_count}` for runtime, agents, changes, checkpoints, supervision, and AI; `attention_count`; latest checkpoint recovery facts or `null`. No independent safety judgment. |
| `GET /device/v1/read/runtime` | `items[]` containing only `runtime_type`, `execution_domain_id`, `availability`, `capabilities[]`, `reason_code`, `uncertainty`; plus `environment_components[]` only when backed by verified desktop facts. If no real environment authority exists, return `UNKNOWN` with empty data—never `EMPTY` and never sample versions. |
| `GET /device/v1/read/agents` | `items[]` containing R4 allowlisted `detected_identity`, `role`, `lifecycle`, `confidence`, `execution_domain_id`, bounded workspace binding status/reference, `reason_code`, `uncertainty`. |
| `GET /device/v1/read/changes` | Recent verified change metadata only: `change_id`, `observed_at`, `category`, `result`, `execution_domain_id`, optional supervision session ID, uncertainty, and evidence references. No path, before/after content, command, patch, or arbitrary payload. An absent/unimplemented authority is always `UNKNOWN` with empty data; `EMPTY` is allowed only after an implemented authoritative query succeeds and proves zero records. |
| `GET /device/v1/read/checkpoints` | `items[]` with checkpoint ID, execution domain, authoritative status/reason, requested/authorized/intact/test-restored counts, recovery level, R1/R2/R3 booleans, test-restore status, trusted-baseline status/ID, and evidence refs. Checkpoint labels are omitted unless separately sanitized. |
| `GET /device/v1/read/supervision` | `items[]` with session ID, status, deterministic policy decision, requires-manual-approval, requires-checkpoint, recorded manual approval fact, bounded AI assessment, recovery facts, and evidence refs. Omit Core `action_ref`; no approve/reject routes exist on the gateway. |
| `GET /device/v1/read/ai-advisory` | `deterministic_policy` and `advisory` are separate objects. Minimum advisory fields are state, decision, severity, reason code, and evidence refs from desktop-produced facts. No provider, model endpoint, API key, provider configuration, prompt, or direct mobile AI request. AI may not upgrade deterministic policy authority. |
| `GET /device/v1/read/connection` | Authenticated server facts: protocol version, desktop UUID, bound Android UUID, permission `read`, gateway LAN generation, and token expiry. Android overlays local states such as connecting, authenticated, unreachable, revoked, and endpoint-changing. |

The initial adapter should consume `R4ReadProjectionService` through typed Python calls. It may map R4 `status` to mobile `state` but must retain R4 `reason_code`, evidence references, uncertainty, recovery level, test-restore status, and trusted-baseline status without optimistic rewriting. It must not pass through `action_ref` or add mobile mutations.

### 10.3 Error vocabulary

Reachable gateway failures use a bounded envelope:

```json
{"schema_version":"device-link-error/1","code":"TICKET_EXPIRED","retryable":false}
```

No raw exception/detail field is permitted. Stable codes include:

- QR/local parsing: `QR_MALFORMED`, `QR_EXPIRED`, `PROTOCOL_UNSUPPORTED`, `ENDPOINT_OUTSIDE_ACTIVE_SUBNET`.
- LAN: `LAN_UNAVAILABLE`, `LAN_AMBIGUOUS`, `LAN_PROFILE_UNSUPPORTED`, `LAN_EXPOSURE_DEGRADED`, `GATEWAY_UNREACHABLE`.
- Pairing: `PAIRING_NOT_ENABLED`, `PAIRING_SESSION_NOT_FOUND`, `PAIRING_SESSION_MISMATCH`, `TICKET_INVALID`, `TICKET_EXPIRED`, `TICKET_ALREADY_CLAIMED`, `TICKET_REPLAYED`, `DESKTOP_IDENTITY_MISMATCH`, `TLS_IDENTITY_MISMATCH`, `SAS_REJECTED`, `PAIRING_EXPIRED`, `DEVICE_ALREADY_BOUND`, `PAIRING_STATE_INVALID`, `PAIRING_PERSISTENCE_FAILED`.
- Auth: `AUTH_CHALLENGE_EXPIRED`, `AUTH_CHALLENGE_REPLAYED`, `AUTH_SIGNATURE_INVALID`, `AUTH_IDENTITY_MISMATCH`, `DEVICE_REVOKED`, `SESSION_TOKEN_INVALID`, `SESSION_TOKEN_EXPIRED`.
- Endpoint/projection: `ENDPOINT_IDENTITY_MISMATCH`, `ENDPOINT_ENUMERATION_TBD`, `ENDPOINT_ENUMERATION_TIMEOUT` (reserved until a transport gate defines it), `CORE_PROJECTION_UNAVAILABLE`, `PROJECTION_DEGRADED`.

Use HTTP 400 for malformed supported-version requests, 401 for invalid/expired read sessions, 403 for revoked or identity/signature mismatch, 404 for opaque nonexistent resources, 409 for state/replay conflicts, 410 for expired pairing/challenge resources, 426 for unsupported protocol version, 429 for rate limits, and 503 for LAN/persistence/projection authority failure. Responses must not distinguish unknown-ticket from wrong-ticket in a way that helps enumeration; the internal reason may be more specific than the network code.

## 11. Android persistence and repository model

V1 stores exactly one bound desktop record in typed Proto DataStore (or an equivalently typed, atomic app-private store) and one non-exportable P-256 key in Android Keystore. The durable record contains:

- schema version `device-link-binding/1`;
- Android UUID and Keystore alias;
- desktop UUID;
- full desktop P-256 identity DER SPKI and its 64-character fingerprint;
- gateway TLS SPKI fingerprint;
- protocol version;
- last known canonical IPv4 and fixed port 8788;
- last observed gateway generation (diagnostic only; a lower/reset value never rejects auth, candidate verification, or endpoint update);
- binding timestamp and optional sanitized desktop display name;
- local binding state `ACTIVE` or `REVOKED`.

It never stores the raw QR URI, authorization ticket, pairing secret, SAS, provider configuration, API keys, read bearer token, or raw Core response. The read token remains in memory. Before finalize, pending pairing state is expiry-bound. Immediately before a finalize attempt it becomes `FINALIZE_UNCERTAIN` and is retained across restart until a signed receipt promotes it, the desktop authoritatively proves that no binding committed, or the user explicitly resets it. This recovery state contains only the non-authoritative fields allowed in section 6.4 and cannot authenticate reads by itself.

One repository owns connection state and converts transport failures to `UNREACHABLE`. Screens consume sealed projection states rather than constructing demo defaults. On app restart with an active binding, the repository tries the saved endpoint and mutual challenge; if that fails it invokes only a separately approved enumerator, otherwise reports `ENDPOINT_ENUMERATION_TBD`. It never launches QR or asks for SAS merely because an endpoint changed.
### 11.1 Android permission and network API contract

The app may retain its frozen minimum SDK 26, but the Device Link feature is supported only when `Build.VERSION.SDK_INT >= 29`, where the platform TLS stack provides TLS 1.3. On API 26–28 the Device Link entry renders `DEVICE_LINK_OS_UNSUPPORTED`, requests no camera/network operation, and cannot pair or reconnect. V1 has no TLS 1.2 fallback and does not install a dynamic TLS provider.

For target SDK 34, the manifest surface is exact:

| Permission/feature | V1 rule |
| --- | --- |
| `android.permission.INTERNET` | Required normal permission for pinned HTTPS to the known/candidate gateway endpoint. |
| `android.permission.ACCESS_NETWORK_STATE` | Required normal permission for `ConnectivityManager` and `LinkProperties`. |
| `android.permission.CAMERA` | Required dangerous permission only while the user enters first-pair scanner flow; request at runtime with rationale/retry. |
| `android.hardware.camera.any` | Declare with `required=false` so unsupported devices can install and report `CAMERA_UNAVAILABLE`. |
| Cleartext traffic | Set `usesCleartextTraffic=false`; remove emulator cleartext exceptions from production. |
| Enumeration-specific permissions | TBD at the separate bounded design gate; none are authorized by this contract. The gate may not introduce background location, a permanent scanner, or a foreground service to evade bounded execution. |

The repository accepts exactly one active `Network` whose `NetworkCapabilities` has `TRANSPORT_WIFI` or `TRANSPORT_ETHERNET`, has `NET_CAPABILITY_NOT_VPN`, and does not have `NET_CAPABILITY_CAPTIVE_PORTAL`. Cellular, VPN (including a VPN over Wi-Fi), Bluetooth, Wi-Fi Aware, LoWPAN, absent, and multiple eligible networks fail closed. Internet/validated capability is not required because the service is LAN-local. `getLinkProperties(network).linkAddresses` must yield exactly one eligible RFC1918 IPv4/prefix; zero or multiple values return `ANDROID_LAN_UNAVAILABLE` or `ANDROID_LAN_AMBIGUOUS`. The repository binds the HTTPS socket to that exact `Network` and requires the QR/verified candidate to lie in the computed subnet. It does not read SSID/BSSID or location for the frozen pairing/verification flow. The separate enumeration gate must declare any additional API or permission before code changes. The scanner uses a local CameraX/offline barcode decoder and does not upload frames.

Camera denial returns `CAMERA_PERMISSION_REQUIRED`; missing hardware returns `CAMERA_UNAVAILABLE`. The user may retry the permission flow, but v1 supplies no manual-address normal fallback. Missing/ambiguous active-network or IPv4/prefix facts returns `LAN_FACTS_UNAVAILABLE`, preserves any durable binding, and performs no discovery or endpoint update. Permission and capability failures render truthful local state rather than demo data.



## 12. Minimum regression and acceptance matrix

Every row is required before the future implementation can claim Device Link v1 behavior. Unit tests use fixed clocks/RNG and cross-language vectors; network assertions require real sockets. Windows listener/firewall behavior and Android Keystore/scanner behavior cannot be replaced by mocks.

### 12.1 Pairing and QR

| ID | Required assertion |
| --- | --- |
| `PAIR-01 valid-ticket` | Canonical QR, pinned TLS/desktop identities, valid key proof, matching SAS, local match, signed finalize produce exactly one durable read-only binding and consumed ticket. |
| `PAIR-02 expired-ticket` | `now == expires_at` and later fail; no binding/token; secret cleared. |
| `PAIR-03 replayed-ticket` | Second claim, concurrent claim, and post-bind replay fail without changing the first identity. |
| `PAIR-04 wrong-session` | Valid ticket with another session ID fails and advances neither session. |
| `PAIR-05 malformed-qr` | Wrong scheme/path/order, version disagreement, duplicate/unknown/missing field, percent encoding, overlength, invalid IPv4/UUID/hex/base64, or wrong port yields `QR_MALFORMED`; equal canonical unsupported versions yield `PROTOCOL_UNSUPPORTED`; both cause zero network I/O. |
| `PAIR-06 wrong-fingerprint` | Wrong TLS or desktop identity fingerprint stops before ticket submission/binding. |
| `PAIR-07 sas-reject` | Desktop-local reject makes terminal `REJECTED`, invalidates flow/ticket, and leaves no binding. LAN cannot invoke confirm. |
| `PAIR-08 sas-expiry` | Expiry before or during local confirmation/finalize fails terminally with no binding. |
| `PAIR-09 successful-bind` | Durable desktop registry and Android record contain exact matching identities/read permission; restart uses auth without QR/SAS. |
| `PAIR-10 sas-vectors` | Python and Kotlin independently produce one literal expected SAS from the exact binary vector; mutating every transcript field changes verification outcome. |
| `PAIR-11 atomicity-and-recovery` | Fault injection at DB/Evidence commit, receipt generation, response delivery, Android persistence, desktop restart, and Android restart produces either no binding or recoverable same-key receipt state. Recovery never repeats QR/SAS/confirmation, binds a second key, or leaks a ticket. |
| `PAIR-12 single-device` | A second pairing session is refused while one active device is bound. |
| `PAIR-13 signed-vectors` | Python and Kotlin share literal vectors for CLAIM, CLAIM_RESPONSE, FINALIZE, BINDING_RECEIPT, RECEIPT_RECOVER, and RECEIPT_ACK; changing each field or appending bytes fails verification. |

### 12.2 Authentication

| ID | Required assertion |
| --- | --- |
| `AUTH-01 valid-challenge` | Both signatures, IDs, endpoint, protocol, expiry, and TLS binding verify; one read token is issued. |
| `AUTH-02 wrong-signature` | Wrong Android or desktop signature fails; challenge is consumed on first attempt. |
| `AUTH-03 expired-challenge` | Exact expiry boundary fails with no token. |
| `AUTH-04 replay` | Reused, reordered, cross-device, or cross-endpoint challenge fails. |
| `AUTH-05 revoked-device` | Revocation invalidates outstanding challenges/tokens and prevents every candidate from passing endpoint verification. |
| `AUTH-06 expired-session-token` | Read request at expiry returns typed 401; fresh challenge works without QR/SAS. |
| `AUTH-07 restart` | Desktop and Android restarts retain durable identity/binding, discard transient tokens, and reconnect by challenge only. |
| `AUTH-08 signed-vectors` | Python and Kotlin share literal AUTH_CHALLENGE/AUTH_REQUEST/AUTH_RESPONSE bytes and signatures; every field, length, endpoint, TLS fingerprint, token hash, expiry, and trailing-byte mutation fails. |

### 12.3 Network boundary and LAN Sync

| ID | Required assertion |
| --- | --- |
| `NET-01 core-loopback` | Core owns only `127.0.0.1:8787`; LAN address and `0.0.0.0:8787` refuse. |
| `NET-02 gateway-physical` | Gateway HTTPS TCP socket owns only the selected physical IPv4:`8788`; listener ownership and `getsockname()` match adapter facts. No enumeration socket is implied. |
| `NET-03 no-wildcard` | Socket enumeration proves no `0.0.0.0`/`::` listener for either service. |
| `NET-04 route-isolation` | Every Core-only `/api/*`, session bootstrap, mutation, SPA, and unknown route is unavailable from `8788`; `/device/*` is unavailable from `8787` except authenticated desktop-local pairing control routes under the Core namespace. |
| `NET-05 firewall-scope` | The owned HTTPS TCP rule matches exact program/local IP/remote subnet/8788/Private profile; unrelated rules remain unchanged. Enumeration firewall policy awaits its separate gate. |
| `NET-06 public-or-ambiguous` | Public, zero-candidate, multiple-candidate, virtual/VPN-only, or malformed-prefix conditions disable exposure without fallback. |
| `NET-07 resync-rollback` | DHCP/profile/interface change closes old exposure, increments generation, replaces owned rules, and leaves no old listener/rule; injected failure is fail-closed and reports residual state. |
| `NET-08 native-boundary` | Gateway has no CORS middleware, WebView CSP is not widened for LAN, and Android rejects cleartext HTTP. |
| `NET-09 android-platform` | API 26–28 returns `DEVICE_LINK_OS_UNSUPPORTED`; API 29+ TLS 1.3 is enforced. Camera grant/deny/unavailable and allowed Wi-Fi/Ethernet versus cellular/VPN/captive/zero/multiple IPv4 facts produce exact fail-closed states. No enumeration-specific permission appears before its gate. |
| `NET-10 limits` | Every body, field, concurrency, rate, timeout, and response-size boundary in section 6.5 passes at the limit and fails one over without unbounded state. |

### 12.4 Reconnect and endpoint candidate verification

These rows exercise the frozen interface and trust invariant with injected candidate sequences. They do not select or certify a transport. The separate design gate must add its own real transport tests.

| ID | Required assertion |
| --- | --- |
| `RECON-01 last-endpoint` | Saved endpoint + TLS pin + fresh mutual challenge succeeds without invoking enumeration, QR, SAS, or confirmation. |
| `RECON-02 endpoint-changed` | Old DHCP address failure preserves durable binding/endpoint history, never reports SAFE, and either invokes an approved enumerator or returns `ENDPOINT_ENUMERATION_TBD`. |
| `RECON-03 candidate-not-trust` | A candidate alone—including one on the local subnet or from the last transport observation—never changes endpoint, token, or binding state. |
| `RECON-04 wrong-identity` | Candidate with wrong UUID, desktop key/fingerprint, TLS pin, signature, challenge context, expiry, or revoked binding is rejected and stored endpoint is unchanged. |
| `RECON-05 verified-update` | Only pinned TLS plus a fresh endpoint-bound mutual challenge against the saved identity atomically creates `VerifiedEndpoint` and updates the endpoint. |
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
| `PROJ-05 unreachable` | Transport/TLS/auth failure becomes Android-local `UNREACHABLE`, never SAFE and never a fake server response. |
| `PROJ-06 recovery-truth` | Checkpoint existence, R1/R2/R3, test restore, and trusted baseline are displayed as separate facts; `R3` alone does not render TRUSTED/recoverable. |
| `PROJ-07 policy-ai-separation` | Deterministic policy and AI advisory remain separate; AI cannot upgrade policy and no provider secret/config reaches Android. |
| `PROJ-08 read-only` | Gateway route enumeration and Android client API contain no POST/PUT/PATCH/DELETE product mutation, approve/reject, checkpoint, restore, recovery, or authority action. |
| `PROJ-09 leakage-and-caps` | Paths, commands, diffs, secrets, action refs, raw rows/payloads, and over-cap items never appear. |
| `PROJ-10 no-fake-production-data` | Production build/screens fail tests if hardcoded Claude/Node/Python/Docker/Tailscale or other demo facts appear without a repository fixture. |

### 12.6 End-to-end gate

The final packaging/E2E gate uses a real Windows desktop installation, real physical/private adapter or controlled Windows network, actual scoped HTTPS firewall rule, the actual `8787` Core and separate `8788` gateway, and an Android emulator/device with Keystore and scanner input. It proves first pair, process/app restart, last-known-endpoint challenge reconnect, real projections, cleanup, and revocation. If—and only if—the separate enumeration gate has approved and implemented a transport, the E2E also proves DHCP endpoint change through that real transport. Hand-built routers, precomputed SAS from server internals, cleartext emulator-only traffic, or mocked adapter facts are not substitutes for the behaviors claimed.

## 13. Explicit v1 non-goals

The following are rejected for v1, not merely postponed within an implementation commit:

- generic “find computers” or device-directory UX; endpoint enumeration remains targeted to the saved bound identity under section 8;
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
| LAN Sync | new platform-neutral `agentguard/device_link/lan.py` and narrow Windows backend `agentguard/device_link/lan_windows.py`; `desktop/src-tauri/src/main.rs`/`sidecar.rs` remain process-lifecycle owners only | Exact interface facts and owned HTTPS TCP rule; Python gateway owns the HTTPS socket and coordinates resync/rollback. Enumeration networking is outside this owner until its gate. |
| Gateway boundary | new `agentguard/device_link/server.py`, `projections.py`, and transport-neutral `endpoint_candidates.py` interface; `agentguard/api/server.py` only to remove current remote mount/add local coordinator; `agentguard/cli.py` only to enforce listener separation | Separate ASGI app, HTTPS 8788, route allowlist, endpoint verifier/interface, internal projection adapter, Core isolation. No enumeration transport implementation. |
| Android transport/persistence | `network/DeviceLinkClient.kt`, new pinned HTTPS/auth/repository and `BoundEndpointCandidateEnumerator` interface files, Manifest/network security config | Native client, Keystore, strict errors, session handling, saved-endpoint verification, and transport-neutral candidate boundary; no enumeration implementation or product mutations. |
| Android pairing UI | New scanner/pairing/SAS screens and ViewModels in the existing Android module | Camera scan, one first-pair flow, truthful states. Coordinate around K3; do not overwrite its presentation shell. |
| Read projections/UI wiring | `R4ReadProjectionService` only through narrow additions if required; gateway projection adapter; Android repositories/screen state | Real read-only data and truthful EMPTY/UNKNOWN/DEGRADED/UNREACHABLE rendering; remove fake defaults. |
| Tests | Python Device Link tests, Windows listener/firewall integration, Android JVM/instrumented tests, final E2E workflow | The matrix in section 12, with real boundary tests where required. |

If Windows firewall ownership or network-change implementation needs privileges not available to the unelevated Tauri process, the LAN owner must resolve that with an install-time/product-owned mechanism and prove cleanup. It must not silently use a broad pre-created rule, shell out to an unspecified external script, or move network policy into Android.

## 15. Recommended atomic implementation sequence after R4 close

Each commit begins with failing tests for its own boundary and stays independently reviewable.

1. **D1 — canonical contracts and vectors.** Add strict Python/Kotlin QR codecs, error/state enums, framed claim/SAS/auth encodings, transport-neutral candidate interfaces, and fixed cross-language vectors. No production listener or enumeration wire format.
2. **D2 — durable identities, one-time ticket, and binding transaction.** Add protected desktop identity/TLS material, single-device store, corrected pairing FSM, desktop-local session creation/confirmation, ticket replay/expiry/atomicity tests. Keep networking loopback/test-harness only.
3. **D3 — LAN Sync adapter and Windows proof.** Implement only the section 7 fact/rule adapter and Windows tests, using short-lived test sockets to prove exact-address compatibility. Do not create persistent production listeners or mount gateway routes yet.
4. **D4 — separate gateway wiring.** Create the isolated HTTPS `8788` ASGI app, pairing claim/finalize, local Core coordinator bridge, strict limits/errors, and negative route/listener tests. Remove `/device/v1` from the Core app; never enable remote Core.
5. **D5 — Android real first-pair flow.** Add Keystore identity, camera scanner, strict QR validation, pinned HTTPS, independent SAS derivation/display, desktop-local confirmation coordination, signed finalize, and binding persistence. Remove cleartext assumptions.
6. **D6 — durable challenge reconnect.** Implement mutual canonical challenge auth, transient bearer tokens, restart behavior, local revocation enforcement, and Android repository reconnect without repeated ceremony.
7. **D7 — separate bounded endpoint-enumeration design gate, then separately approved implementation.** First select and threat-model a transport outside this contract and bind it to section 8's interface/tests. No transport code, permission, listener, or firewall rule may land until that gate is approved; any later implementation is its own atomic commit.
8. **D8 — real read projections and UI state wiring.** Add the bounded gateway DTO adapter and Android repositories/screens; delete production demo defaults; preserve deterministic policy/recovery/AI semantics and omit actions.
9. **D9 — real Windows/Android E2E and packaging gate.** Exercise install, first pair, restart, last-endpoint reconnect, real read data/state failures, revocation, process/firewall cleanup, and uninstall using actual binaries/sockets. Add DHCP-change enumeration proof only after D7's separate gate and implementation exist.

Do not start D1 before R4 closes. Do not merge K3 opportunistically into protocol or backend commits. If K3 becomes available, reconcile only presentation-owned files after its exact SHA is verified.

## 16. Acceptance summary and non-claims

This contract establishes an implementation boundary and test plan. It confirms reusable P-256/SAS/FSM and R4 read-projection primitives, but it also confirms that production LAN exposure, one-time QR authorization, durable trust, Android security/persistence, and real mobile projections are not currently implemented. Endpoint candidate enumeration transport is intentionally TBD at a separate bounded design gate and is not a missing deliverable of this contract.

This document does not claim Device Link implemented, R4 complete, L4/L5/L6 pass, P9 pass, Product Integration started, or product readiness. The next action is **WAIT FOR R4 CLOSE**.
