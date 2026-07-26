# Device Link Pairing Protocol

> **FUTURE FLOW.** P0-A preserves the current SAS implementation but does not
> verify independent cross-participant or double confirmation. The executable
> runtime contract is in `API_CONTRACT.md` and `PROTOCOL.md`.

## Overview
One-time device pairing using QR code + Short Authentication String (SAS).

## Preconditions
- Desktop Device Link enabled (Ed25519 key generated, mDNS advertising)
- Android on same LAN, NsdManager active
- Both devices on Private Network (not Public)

## Pairing Flow

### Step 1: Desktop Initiates
```
Desktop UI: [Pair Android Device]
→ Generates: pairing_session_id (16 bytes random)
→ Generates: pairing_secret (32 bytes random)
→ Sets 120s expiry
→ QR content (agentstate:// URI):
    protocol=1
    &host=<lan-ip>
    &port=<gateway-port>
    &uuid=<desktop-uuid>
    &pk=<base64url-desktop-public-key>
    &fp=<sha256-fingerprint-first-8-hex>
    &sid=<pairing-session-id>
    &exp=<expiry-unix-ts>
```

### Step 2: Android Scans
```
Android Camera → QR decode
→ Extract: host, port, uuid, pk, fp, sid, exp
→ Check: expiry not passed
→ Check: UUID not already bound
→ Open: TLS connection to host:port
→ Verify: cert public key fingerprint == fp from QR
```

### Step 3: SAS Display
```
Desktop generates: 6-digit SAS from HKDF(pairing_secret, "SAS")
Android computes: same 6-digit SAS from HKDF(pairing_secret, "SAS")
Both display: "482 913"
```

### Step 4: Dual Confirmation
```
Desktop: user taps [Confirm] or [Reject]
Android: user taps [Confirm] or [Cancel]
→ If mismatch → pairing failed
→ Timeout after 180s → pairing expired
→ Either reject → pairing cancelled
→ 3 SAS failures → session invalidated
```

### Step 5: Key Exchange
```
Android sends to Desktop (over TLS):
  { android_uuid, android_public_key, display_name, protocol_version }

Desktop verifies:
  android_uuid is new (not already bound in Single mode or replace)
  android_public_key is valid Ed25519

Desktop sends to Android (over TLS):
  { desktop_uuid, desktop_public_key, display_name, permissions }

Android stores locally:
  { desktop_uuid, desktop_pub_key_fp, display_name, gateway_host }
```

### Step 6: Binding Stored
```
Desktop stores:
  device_uuid, public_key, public_key_fingerprint, display_name,
  created_at, permissions, protocol_version

Android stores:
  device_uuid, public_key, public_key_fingerprint, display_name,
  gateway_endpoint, created_at

Session token issued: 32-byte random, 3600s TTL
```

## Cancellation
- Desktop closes pairing dialog → session invalidated
- Android navigates away → no binding created
- 120s expiry → session auto-invalidated

## Rejection
- User taps [Reject]/[Cancel] → binding not created
- No partial state persists

## Security Properties
- QR is necessary but NOT sufficient (SAS defeats camera observation)
- SAS is necessary but NOT sufficient (TLS ensures the right Desktop)
- Single-use session prevents QR replay
- Expiry prevents offline QR brute-force
- 3-attempt SAS limit prevents guessing
