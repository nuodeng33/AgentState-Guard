# Device Link Cryptography — Updated Architecture

> **DESIGN TARGET, NOT CURRENT RUNTIME.** For behavior implemented and
> verified in P0-A, use `API_CONTRACT.md`, `PROTOCOL.md`, and
> `ACCEPTANCE_MATRIX.md`. TLS, LAN discovery, pinning, Keystore, and other
> future guarantees below are not yet implemented.

## Key Algorithms (corrected)

| Purpose | Algorithm | Platform |
|---------|-----------|----------|
| Device Identity Signing | ECDSA P-256 + SHA-256 | Android Keystore / Desktop Rust/Python |
| TLS Server Identity | ECDSA P-256 or RSA-2048 | Desktop self-signed |
| SAS Derivation | HMAC-SHA256(secret, transcript) | Both platforms |
| Pairing Secret | 256-bit CSPRNG | Both platforms |
| Session Key Exchange | TLS 1.3 (Ephemeral) | TLS library |
| Challenge Nonce | 256-bit CSPRNG | Both platforms |
| Fingerprint | SHA-256 of SPKI (SubjectPublicKeyInfo DER) | Both platforms |

## Key Purpose Separation (MANDATORY)

1. **Device Identity Signing Key** — Signs challenges, pairing transcripts, never used for TLS
2. **TLS Server Identity** — Only for TLS handshake
3. **Ephemeral Session Keys** — Per-connection, never persisted

Do NOT reuse the same key for different purposes.

## Android Private Key Storage (corrected)
- Long-term keys: AndroidKeyStore (EC P-256, non-exportable)
- Non-sensitive metadata (UUID, display name, pubkey, fingerprint, permissions): SharedPreferences / DataStore / SQLite
- Secrets requiring local persistence: AES-GCM key from AndroidKeyStore → encrypt before disk

## Desktop Private Key Storage (corrected)
- Windows: DPAPI (`CryptProtectData`)
- Linux (headless dev): file with 0600 permissions, optional passphrase
- macOS (future): Keychain

## SAS Derivation (corrected)
```
transcript = canonical_concat([
    protocol_version,
    pairing_session_id,
    desktop_uuid,
    android_uuid,
    desktop_device_pubkey,
    android_device_pubkey,
    desktop_tls_spki_fingerprint,
    nonce_desktop,
    nonce_android,
    expiry
])
SAS_raw = HMAC-SHA256(pairing_secret, transcript)
SAS = to_6digit(SAS_raw)
```
Both sides MUST compute the exact same canonical transcript, or SAS will not match.

## Pairing Token State Machine (corrected)
```
CREATED → FIRST_CONNECTION → SAS_PENDING → CONFIRMED_BOTH → CONSUMED
                                                               ↓
          EXPIRED / REJECTED / FAILED / CANCELLED              (terminal)
```
Once terminal: secret permanently invalid. Never reactivated. One session = one binding.

## TLS Pinning (corrected)
- Android pins SPKI (SubjectPublicKeyInfo) SHA-256 fingerprint
- Desktop TLS cert may rotate, but new SPKI → explicit re-pair
- Certificate change without re-pair → CONNECTION REJECTED
- Auto-accept: NEVER

## Challenge Authentication (corrected)
Challenge-signed context binds:
```
protocol_version || session_id || client_uuid || server_uuid || challenge_nonce || timestamp || tls_channel_hash
```
Replay cache: single-use, expiry-enforced.
