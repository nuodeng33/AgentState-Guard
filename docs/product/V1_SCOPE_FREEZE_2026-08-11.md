# AgentState Guard v1 Scope Freeze

Date: 2026-08-11

Status: **FROZEN V1 SCOPE — DESIGN AND ACCEPTANCE ORDER ONLY**

Documentation base: **f9731426e1d4433aeeb5ec50b6d887f705ab8b42**

Base tree: **a113fce6d843b049e2cc5ffb013bfd8b012f5a02**

This document is the controlling v1 product-scope boundary. Where an older
product-integration, Device Link, networking, Android, or AI planning document
conflicts with this freeze, this document governs v1. Historical documents
remain provenance; they do not authorize implementation, mutation, authority
changes, or release claims.

This freeze does not modify R4, P7, P8, recovery authority, Evidence Ledger
semantics, or the evidence ladder. Every acceptance and release decision remains
bound to one exact product SHA.

## 1. Current state

### R4

- R4 Core P0-P8 is complete and frozen.
- The packaged Tauri routing blocker implementation is fixed and accepted in
  candidate f9731426e1d4433aeeb5ec50b6d887f705ab8b42. That blocker result is
  verified at L3 and does not satisfy the remaining R4 acceptance gates.
- Core exact-SHA CI and Desktop Windows exact-SHA CI passed for that candidate.
- The installed-MSI/main-Tauri hosted smoke passed at L3 and demonstrated
  packaged WebView bootstrap traffic reaching Core.
- GitHub-hosted Windows is L3. It is not L5.
- L5 is **NOT RUN**.
- L4 is **BLOCKED** pending a fresh trustworthy environment and evidence set.
- L6 is **NOT RUN**.
- P9 is **NOT COMPLETE**.
- R4 COMPLETE and PRODUCT READY are **NOT CLAIMED**.
- The historical C-worker package marked
  **HOST_PACKAGE_INCOMPLETE — DO NOT RUN** remains quarantined. It must not be
  repaired, reused, or described as an executable L4 package.

R4 closure order is immutable:

1. Real Windows L5.
2. Fresh trustworthy L4.
3. L6.
4. P9 tribunal.
5. R4 COMPLETE decision.

Formal Product Integration starts only after that sequence. UI-only work may
remain isolated on its own branch, but it must not be merged into the frozen R4
candidate.

### K3 Product UI and i18n handoff

The reported K3 handoff is:

- branch: feat/product-ui-i18n-k3
- base: cfdf9f08477b07f2999c422ba081465cee00d072
- reported HEAD: 822acf6dbaf00f74879085cb88f160273971c7dd

In the 2026-08-11 recovery environment, the reported worktree, local ref, remote
ref, and commit object were unavailable. The reported nine-commit diff, test
results, and forbidden-path claims are therefore **NOT VERIFIED** and must not
be treated as durable state until the exact commit chain is recovered and
independently audited.

Any Devices, SAS, Find LAN, manual-IP, or legacy onboarding controls present in
an isolated UI shell are presentation placeholders only. They are **NOT V1
PRODUCTION BEHAVIOR**. Future Device Link wiring must remove or hide controls
that conflict with this freeze. This does not authorize rewriting the historical
K3 branch during recovery.

## 2. V1 core user flow

The v1 product loop is limited to:

1. Windows install.
2. Installed Tauri main starts.
3. Loopback Core 8787 becomes ready.
4. Runtime, agent, supervision, and recovery truth is visible.
5. Optional AI advisory.
6. User enables Device Link.
7. LAN Sync.
8. First QR authorization.
9. First SAS confirmation.
10. Durable device binding.
11. Android receives real read-only projections.
12. App restart and automatic cryptographic challenge reconnect.
13. Physical adapter or IP change and automatic bound-device endpoint recovery.
14. No repeated QR or SAS.
15. Dual-device E2E.
16. Release-candidate evaluation.

If removing a proposed capability leaves this loop intact, that capability is
out of v1 by default.

## 3. Desktop authority and network boundaries

### Core API — 127.0.0.1:8787

- Core remains loopback-only.
- Core is the sole local authority.
- Android never accesses the complete 8787 API directly.
- Device Link must not make Core remotely reachable through forwarding,
  tunnelling, route mirroring, or a transparent proxy.

### Device Link — CURRENT_PHYSICAL_LAN_IP:8788

- Device Link is a distinct LAN Gateway with bounded projections.
- It binds directly to the current physical LAN IPv4 address on port 8788.
- Binding 0.0.0.0:8788 is prohibited.
- No broader bind or portproxy is allowed within this v1 scope. If a real
  platform constraint later proves direct interface bind impossible, work stops
  until this freeze is explicitly amended through a separate review.
- Port 8788 is never a transparent Core API proxy or a second authority.

Existing /device/v1 code and legacy documents are technical antecedents, not
proof that the final v1 bind, route surface, authorization, or acceptance
contract has already been implemented.

## 4. Android v1 is read-only

Android v1 may read only bounded projections for:

- runtime and environment;
- agents, if required by the product UI;
- changes;
- checkpoints;
- truthful recovery status;
- supervision status;
- AI advisory;
- device and connection state.

Android v1 must not perform:

- Approve Once or Reject mutations;
- recovery, restore, or undo mutations;
- transaction or arbitrary Core API mutations;
- policy mutations;
- checkpoint creation;
- Trusted Baseline mutations;
- any P7 or P8 authority operation;
- storage of AI provider credentials;
- creation of authoritative state.

UNKNOWN, UNREACHABLE, OFFLINE, BLOCK, and recovery capability states remain
truthful. None may be presented as SAFE, ALLOW, RECOVERABLE, or TRUSTED without
the corresponding authoritative evidence.

## 5. Device Link v1

The only v1 Device Link model is:

**LAN Sync + QR one-time authorization + durable cryptographic device identity
+ automatic reconnect.**

The following former discovery paths are removed from v1:

- mDNS;
- Android NSD;
- generic LAN discovery;
- Find computers on LAN as a production path;
- manual IP as a normal onboarding path.

They must not establish trust or appear as supported v1 production behavior.

## 6. LAN Sync

LAN Sync serves only Device Link. It performs the minimum bounded sequence:

1. Detect the active physical LAN adapter.
2. Determine IPv4 address and prefix.
3. Calculate the local subnet.
4. Bind Device Link to that IPv4 on 8788.
5. Install or update a scoped Windows Firewall rule.
6. Record truthful state.
7. Clean up or roll back only owned changes.
8. Resynchronize after adapter, profile, prefix, or IP change.

For 192.168.1.23/24, the intended boundary is:

- bind: 192.168.1.23:8788
- firewall TCP: 8788
- LocalAddress: 192.168.1.23
- RemoteAddress: 192.168.1.0/24

LAN Sync must not become a generic Windows firewall manager, generic network
profile manager, generic discovery subsystem, or unrelated host-cleanup tool.

## 7. First pairing: QR authorization and SAS

The first-pair sequence is frozen as:

1. User selects Add Mobile Device.
2. LAN Sync resolves and scopes the physical LAN endpoint.
3. A bounded pairing session starts.
4. A one-time authorization ticket is created.
5. Desktop renders QR.
6. Android scans QR.
7. The existing P-256 pairing flow runs.
8. Both sides show SAS.
9. The user performs one-time explicit confirmation.
10. Durable cryptographic device binding is established.
11. The ticket is consumed permanently.

The QR payload contains at least:

- protocol version;
- host and port;
- desktop UUID;
- desktop fingerprint or SPKI identity;
- pairing session ID;
- expiry;
- one-time authorization ticket.

The ticket must be:

- generated by a high-entropy CSPRNG;
- one-time, pairing-session-bound, and expiry-bound;
- consumed on successful binding;
- invalidated on rejection or expiry;
- rejected on replay;
- never used as a durable credential.

Ticket state transitions must be atomic. At most one live pairing attempt may
reserve a ticket for its originating pairing session; concurrent or later
redemption is rejected. SAS rejection invalidates the ticket. A disconnect
cannot transfer it to another session, and the original expiry remains in force.

The existing pairing TTL of approximately 120 seconds should be retained unless
a concrete protocol defect requires separate review. Existing P-256 and pairing
state-machine semantics must not be casually rewritten.

SAS is mandatory for the first pair, is never auto-confirmed, and happens only
once for the durable device relationship:

- QR = one-time user authorization.
- SAS = one-time human identity confirmation.
- Device key = durable identity.
- Challenge = subsequent automatic authentication.

## 8. Durable reconnect and endpoint change

Normal reconnect must not repeat QR, SAS, or manual confirmation. It uses:

- saved desktop UUID;
- saved desktop fingerprint;
- last-known endpoint;
- AndroidKeyStore private key;
- a fresh server challenge;
- a signed response;
- a fresh bounded session token after identity verification.

A session token is not durable trust.

DHCP, physical adapter, profile, prefix, or IPv4 change must not require a new
pair. LAN Sync updates the desktop bind and firewall state. Android performs
**bound-device endpoint rediscovery** only for an already bound desktop UUID.

An endpoint candidate is not trust. Before last-known endpoint may be updated,
the candidate must prove all of:

- saved desktop UUID;
- saved desktop fingerprint;
- fresh cryptographic challenge.

A responsive machine with the wrong identity must never update the endpoint or
establish a session.

This freeze deliberately does not select a candidate-enumeration transport for
bound-device endpoint rediscovery. That mechanism requires a later, separately
reviewed bounded design before implementation. It must not reintroduce mDNS,
NSD, generic discovery, or manual-IP onboarding. Regardless of reachability
mechanism, no endpoint update occurs before UUID, fingerprint, and fresh
challenge verification.

## 9. Minimum network and identity regression boundary

V1 must retain focused tests proving:

- 8787 remains loopback-only;
- 8788 is not bound to 0.0.0.0;
- the 8788 listener equals the selected current physical LAN IPv4 and is absent
  from other, virtual, loopback, wildcard, and IPv6-wildcard interfaces;
- firewall LocalAddress and RemoteAddress equal the selected IPv4 and subnet;
- no portproxy path exposes 8788;
- an unauthorized device is denied;
- an invalid session token is denied;
- a wrong signature is denied;
- challenge replay is denied;
- an expired challenge is denied;
- ticket replay is denied;
- ticket expiry is denied;
- identity substitution is denied;
- Core-only APIs are unavailable through 8788;
- a valid bound-device reconnect succeeds;
- an endpoint change with the wrong identity cannot update last-known endpoint.

Reuse focused existing crypto and pairing tests where they establish these exact
properties. V1 does not create an independent general network/security audit
workstream or a generic network test framework.

## 10. AI v1 feature freeze

The existing AI architecture remains in force:

- OpenAI-compatible provider and DeepSeek preset;
- custom endpoint, connection test, and model list;
- structured analysis through AISupervisor;
- Evidence Ledger binding;
- deterministic policy ownership;
- advisory-only AI semantics;
- fail-closed fallback.

Do not redesign AI authority. The only remaining AI v1 product tasks are:

### AI-1 — Provider UI

The production UI may configure provider, base URL, API key, model, and test
connection. The API key is memory-only. V1 does not add a secret vault.

### AI-2 — Analyze Current Environment

The frontend may request analysis of current state. The server, not the caller,
collects authoritative runtime, agents, supervision, recovery, and evidence,
sanitizes that material, and passes the bounded context to AI. The caller cannot
construct or override authority context.

### AI-3 — Full Advisory Presentation

The UI separates deterministic policy from AI advisory and may display severity,
summary, uncertainties, recommended or required checks, evidence references,
and provider or model identity where appropriate.

AI must never approve, restore, execute, auto-fix, create authority, upgrade
BLOCK, upgrade UNKNOWN, or become a source of truth.

After AI-1 through AI-3 are complete, AI v1 enters **FEATURE FREEZE**.

## 11. V1 MUST NOT and deferred backlog

The following are explicitly outside v1:

- Android Approve Once or Reject mutations;
- Android recovery mutation, remote restore, remote undo, or remote transaction;
- mDNS, NSD, generic LAN discovery, or manual-IP normal onboarding;
- persistent Android background connection;
- WebSocket streaming or push notifications;
- multi-device support;
- cloud account, cloud sync, relay, STUN, TURN, or Internet remote access;
- plugin system or complex RBAC;
- generic firewall or network-profile management;
- tray workflow before release-candidate stage; any later tray proposal remains
  outside this freeze and requires a separate post-RC decision;
- AI chat, AI agent, RAG, vector database, or multi-agent debate;
- automatic provider routing or failover;
- continuous background AI;
- Android AI credentials;
- unlimited AI operation coverage;
- defensive complexity justified only by hypothetical advanced attackers.

Deferred work cannot be reintroduced without changing this freeze through an
explicitly reviewed documentation decision.

## 12. Definition of Done

V1 is not complete until all of the following are true on exact reviewed SHAs:

1. R4 completes real Windows L5, fresh trustworthy L4, L6, and P9 tribunal in
   that order.
2. The frozen R4 candidate remains unchanged during acceptance.
3. Any K3 UI and i18n work is durably present, independently scope-audited, and
   integrated only after R4 closure.
4. Windows installation and the installed Tauri/Core truth path pass on the
   intended real target.
5. AI-1 through AI-3 satisfy advisory-only and server-owned-context boundaries.
6. Device Link binds only the scoped physical LAN endpoint and passes the
   minimum negative regression boundary.
7. First QR ticket and first SAS establish one durable device relationship;
   ticket replay, expiry, and identity substitution fail closed.
8. Android receives only real read-only projections and never becomes an
   authority source.
9. Restart reconnect succeeds through a fresh challenge without QR or SAS.
10. IP or adapter change recovers only the bound device endpoint and rejects a
    wrong identity.
11. A real Windows and Android dual-device E2E run produces durable exact-SHA
    evidence.
12. Release-candidate review confirms all explicit non-goals remain absent.

No checkpoint, hosted smoke, script exit code, historical artifact, or planning
document is sufficient by itself to satisfy these gates.

## 13. Workstream order

1. Preserve f973 and execute real Windows L5.
2. Produce a fresh trustworthy L4 envelope.
3. Execute L6 in that envelope.
4. Conduct P9 tribunal and only then decide R4 COMPLETE.
5. Recover and independently verify durable K3 UI and i18n state before
   considering integration; do not reconstruct it from handoff claims.
6. Start formal Product Integration from the closed R4 baseline.
7. Complete AI-1 through AI-3 without altering authority.
8. Implement the narrowly frozen LAN Sync, first-pair, and reconnect Device Link
   path without expanding Android beyond read-only.
9. Run focused network and identity regressions and real dual-device E2E.
10. Enter release-candidate evaluation.

No workstream may treat an earlier preparation artifact as the acceptance result
of a later gate.
