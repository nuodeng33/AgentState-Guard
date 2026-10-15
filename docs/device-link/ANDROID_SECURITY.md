# Android Security Architecture

## Keystore
- Ed25519 key pair generated inside Android Keystore
- `KeyGenParameterSpec.Builder` with:
  - `setUserAuthenticationRequired(false)` (app-level auth, not unlock)
  - `setIsStrongBoxBacked(true)` (prefer hardware)
  - `setDigests(KeyProperties.DIGEST_SHA256)`
  - Private key never leaves Keystore — signing operations happen inside
  - Public key exported for pairing exchange

## Storage
- Device binding data stored in EncryptedSharedPreferences
  - `desktop_uuid, desktop_pubkey_fingerprint, display_name, gateway_hint`
- Session tokens stored in EncryptedSharedPreferences with TTL
- No plaintext secrets in SharedPreferences or files

## Permissions
- `INTERNET` — for LAN communication
- `ACCESS_NETWORK_STATE` — to detect LAN connectivity
- `ACCESS_WIFI_STATE` — to verify Private Network
- `NSD` / multicast — for service discovery
- `ACCESS_LOCAL_NETWORK` (API 37+) — future-proof
- `CAMERA` — QR scanning only during pairing
- `BIOMETRIC` — for HIGH_RISK_WRITE confirmation (optional)

## NOT requested
- Location / Fine Location / Coarse Location
- Contacts / SMS / Phone
- Storage (full) / Media
- Bluetooth
- Background location

## Network
- HttpURLConnection with custom TrustManager for certificate pinning
- OkHttp with certificate pinner for production builds
- Rejects all non-TLS connections
- Hostname verification against pinned certificate

## Attacks Mitigated
- APK decompilation → Keys in Keystore, not in code
- SharedPreferences reading → EncryptedSharedPreferences
- MITM → Certificate pinning
- Token theft → Ephemeral tokens, never in URL
- Intent injection → Custom URI scheme validated
