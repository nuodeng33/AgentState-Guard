# ASDL/1 Cryptographic Test Vectors

## Purpose
Gold-standard vectors for cross-platform verification. Python and Kotlin MUST match.

## Vector 1: SAS Derivation
```
protocol_version = 1
pairing_session_id = "a1b2c3d4e5f6a7b8"
desktop_uuid = "d81a3bc2-1111-4aaa-bbbb-222222222222"
android_uuid = "e92b4cd3-3333-4ccc-dddd-444444444444"
desktop_device_pubkey = "3059301306072a8648ce3d020106082a8648ce3d03010703420004aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
android_device_pubkey = "3059301306072a8648ce3d020106082a8648ce3d03010703420004bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
desktop_tls_spki_fingerprint = "a1b2c3d4e5f6a7b8"
nonce_desktop = "c1c2c3c4c5c6c7c8c9c0d1d2d3d4d5d6d7d8d9d0e1e2e3e4e5e6e7e8"
nonce_android = "f1f2f3f4f5f6f7f8f9f0a1a2a3a4a5a6a7a8a9a0b1b2b3b4b5b6b7b8"
expiry = 9999999999
pairing_secret = "deadbeef00000000deadbeef00000000deadbeef00000000deadbeef00000000"

canonical_transcript = concat_bytes(
    uint16_be(1),                                       # protocol
    utf8("a1b2c3d4e5f6a7b8"),                          # session_id
    utf8("d81a3bc2-1111-4aaa-bbbb-222222222222"),       # desktop_uuid
    utf8("e92b4cd3-3333-4ccc-dddd-444444444444"),       # android_uuid
    hex_decode("30593013..."),                           # desktop_pubkey
    hex_decode("30593013..."),                           # android_pubkey
    hex_decode("a1b2c3d4e5f6a7b8"),                     # tls_fp
    hex_decode("c1c2..."),                               # nonce_desktop
    hex_decode("f1f2..."),                               # nonce_android
    uint64_be(9999999999)                                # expiry
)

SAS_raw = HMAC-SHA256(pairing_secret, canonical_transcript)
SAS = (int.from_bytes(SAS_raw[:3], 'big') % 1000000) formatted as "%06d"
Expected: implementation-defined (both platforms must match)
```

## Vector 2: Challenge Signature
```
protocol_version = 1
session_id = "sess-00000001"
client_uuid = "e92b4cd3-3333-4ccc-dddd-444444444444"
server_uuid = "d81a3bc2-1111-4aaa-bbbb-222222222222"
challenge_nonce = "feedface00000000feedface00000000feedface00000000feedface00000000"
timestamp = 9999999999
tls_channel_hash = ""  # not available in headless test

canonical_challenge = concat_bytes(
    uint16_be(1),
    utf8("sess-00000001"),
    utf8("e92b4cd3-3333-4ccc-dddd-444444444444"),
    utf8("d81a3bc2-1111-4aaa-bbbb-222222222222"),
    hex_decode("feedface..."),
    uint64_be(9999999999),
    utf8("")
)

signature = ECDSA-P256-SHA256(private_key, canonical_challenge)
Expected: verifiable with corresponding public_key
```

## Vector 3: Fingerprint
```
pubkey_der = "3059301306072a8648ce3d020106082a8648ce3d03010703420004aabb..."
fingerprint = hex(SHA256(pubkey_der))
Expected: 64-char hex string
```

## Vector 4: Replay Detection
```
challenge_nonce = "unique-1" → accept, store in cache
challenge_nonce = "unique-1" → reject (replay)
challenge_nonce = "unique-2" → accept
cache expiry = 300s → "unique-1" purged after 300s
```

## Vector 5: SAS Mismatch
```
Same transcript, differ by 1 bit in session_id → SAS differs from Vector 1
```
