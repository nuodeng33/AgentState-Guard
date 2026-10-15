# Environment Capability Matrix — Device Link

| Phase | Description | Status | Notes |
|-------|------------|--------|-------|
| 0 | Architecture docs | HEADLESS_VERIFIED | All docs written, committed |
| 0.1 | Corrections (P-256, Keystore, SAS) | HEADLESS_VERIFIED | Doc updates + crypto test vectors |
| 1 | Tauri project scaffold | SOURCE_IMPLEMENTED | Cargo.toml, config, commands — no build in headless |
| 1-build | Tauri cargo build | NEEDS_TOOLCHAIN | Requires Rust + system libs on Linux |
| 1-Windows | Tauri Windows .exe | NEEDS_WINDOWS | Requires Windows + WebView2 |
| 2 | Python sidecar architecture | BUILDABLE_NOW | agentguard-core module, health endpoint, IPC |
| 2-Linux | Linux sidecar binary | BUILDABLE_NOW | Can build with PyInstaller in container |
| 2-Windows | Windows sidecar binary | NEEDS_WINDOWS | Requires Windows + PyInstaller |
| 3 | Device Link Gateway | BUILDABLE_NOW | agentguard/device_link/ module — all on 127.0.0.1 |
| 3-LAN | Gateway on real LAN interface | NEEDS_WINDOWS | Requires network profile detection |
| 4 | Crypto module | BUILDABLE_NOW | ECDSA sign/verify, SAS, challenge, replay cache |
| 5 | mDNS abstraction | BUILDABLE_NOW | Interface + serializer + mock tests |
| 5-real | Real mDNS multicast | NEEDS_REAL_LAN | Requires LAN + multicast |
| 6 | Android project scaffold | BUILDABLE_NOW | Gradle, Compose module structure |
| 6-build | Android Gradle build | NEEDS_ANDROID_SDK | Requires Android SDK |
| 7 | NSD discovery (Android) | SOURCE_IMPLEMENTED | Code written, build needs SDK |
| 8 | Pairing (Android) | SOURCE_IMPLEMENTED | QR + SAS + key exchange code |
| 9 | Persistence (Android) | SOURCE_IMPLEMENTED | Room/SQLite + SharedPreferences |
| 10 | Auto-reconnect (Android) | SOURCE_IMPLEMENTED | NSD + TLS verify + challenge |
| 11 | Android Dashboard UI | BUILDABLE_NOW | Compose with fake backend |
| 11-real | Android UI on device | NEEDS_ANDROID_DEVICE | Requires physical device |
| 12 | Checkpoints/Diff UI | BUILDABLE_NOW | Compose with fake data |
| 13 | Restore workflow UI | BUILDABLE_NOW | Compose wizard with fake data |
| 14 | Desktop device-management logic | BUILDABLE_NOW | Binding store, revoke, policy |
| 15-21 | Tray/installer/attack tests | SOURCE_IMPLEMENTED / NEEDS_WINDOWS / NEEDS_ANDROID_DEVICE | Varies by phase |

## Status Definitions
| Status | Meaning |
|--------|---------|
| HEADLESS_VERIFIED | Written + tested + committed in current Debian container |
| BUILDABLE_NOW | Source written; can run unit/integration tests now |
| SOURCE_IMPLEMENTED | Code written; build blocked by toolchain |
| NEEDS_TOOLCHAIN | Requires Rust/JDK/Gradle — may be installable locally |
| NEEDS_ANDROID_SDK | Requires Android SDK |
| NEEDS_ANDROID_DEVICE | Requires physical Android device or emulator |
| NEEDS_WINDOWS | Requires Windows OS |
| NEEDS_REAL_LAN | Requires real LAN with multicast |
| NEEDS_GITHUB_ACTIONS | Can only be verified in CI |
