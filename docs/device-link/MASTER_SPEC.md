# AgentState Guard Device Link — Master Specification

## Product
Dual-platform (Desktop + Android) local-first remote control for AgentState Guard.

## Protocol Name
**AgentState Device Link (ASDL/1)**

## Core Principles
1. Zero cloud dependency — works fully offline on LAN
2. Cryptographic device identity — no IP/MAC/name-based trust
3. Local-first pairing — QR + SAS, never cloud account
4. Desktop is the security boundary — Android never bypasses whitelist
5. Offline capable — Internet unplugged, LAN still works

## Deliverables
- Windows native desktop app (Tauri 2 + React + Python sidecar)
- Android native APK (Kotlin + Jetpack Compose + Material 3)
- Zero-config LAN auto-discovery via mDNS/NSD
- QR + SAS one-time pairing
- Ed25519 cryptographic device identity
- TLS + fingerprint pinning + challenge authentication
- WebSocket real-time status push
- System tray + installer for Desktop
- APK build + GitHub Actions CI for Android

## Explicit Non-Goals (this phase)
| NOT implemented | Reason |
|-----------------|--------|
| Internet Remote Access | No STUN/TURN/relay |
| Cloud accounts | No server infrastructure |
| Tailscale/ZeroTier | No VPN dependency |
| iOS / macOS / Linux | Phase 1 scope is Windows + Android |
| Push notifications | No cloud service |
| Public network exposure | LAN only, public network blocked |

## Architecture
```
┌─────────────────────────────────────────────┐
│ Windows Desktop (Tauri 2)                   │
│  ┌────────────────────────────────────────┐ │
│  │ React/Vite UI (shared with web)        │ │
│  ├────────────────────────────────────────┤ │
│  │ AgentState Guard Python Core (sidecar) │ │
│  │  ├─ Core API (127.0.0.1:8787)        │ │
│  │  └─ Device Link Gateway (LAN:8788)     │ │
│  ├────────────────────────────────────────┤ │
│  │ Tauri Rust backend                     │ │
│  │  ├─ Sidecar lifecycle                  │ │
│  │  ├─ Device identity (Ed25519 + DPAPI)  │ │
│  │  ├─ mDNS advertisement                 │ │
│  │  ├─ TLS termination                    │ │
│  │  └─ Device binding store               │ │
│  └────────────────────────────────────────┘ │
│  System Tray + Windows Installer             │
└──────────────────┬──────────────────────────┘
                   │ mDNS: _agentstate._tcp.local
                   │ TLS + wss + challenge-auth
                   ▼
┌─────────────────────────────────────────────┐
│ Android (Kotlin + Jetpack Compose)          │
│  ├─ NsdManager discovery                    │
│  ├─ QR scanner (pairing)                    │
│  ├─ Ed25519 device identity (Keystore)      │
│  ├─ Device binding store                    │
│  ├─ WebSocket client                        │
│  └─ Material 3 UI                           │
└─────────────────────────────────────────────┘
```

## Protocol Stack
```
Application:  AgentState Guard Device API (JSON over HTTPS/wss)
              │
Transport:    TLS 1.3 (self-signed desktop cert, Android key pinning)
              │
Identity:     Ed25519 challenge-response per session
              │
Discovery:    mDNS (_agentstate._tcp.local) — UUID + display name + port only
              │
Network:      TCP over Private LAN (Public network auto-rejected)
```

## Security Invariants
1. Desktop API stays bound to 127.0.0.1 — Device Link Gateway is a separate minimal interface
2. Android cannot bypass Desktop's restore whitelist or sensitive path protections
3. Device identity is Ed25519 public key — never IP, MAC, or hostname
4. All secrets use platform key storage (DPAPI / Android Keystore)
5. mDNS broadcasts contain NO tokens, secrets, paths, or user data
6. QR codes contain only temporary pairing data — expire 120s, single-use
7. SAS confirmation required — no silent binding
8. TLS cert changed = connection rejected — no auto-accept
9. Pairing is exclusive by default — replacing a device requires explicit confirmation
10. Public networks auto-refuse — AgentState Guard doesn't weaken Windows firewall

## Requirement IDs
### DESKTOP
- ASD-ARCH-001: Tauri 2 shell with React webview
- ASD-ARCH-002: Python sidecar as externalBin
- ASD-ARCH-003: Sidecar lifecycle (start/stop/crash recovery)
- ASD-ARCH-004: System tray with minimize-to-tray
- ASD-ARCH-005: Windows installer (.msi/.exe)
- ASD-ARCH-006: No Python/Node/Docker runtime required after install

### DEVICE IDENTITY
- ASD-ID-001: Ed25519 key pair on first Device Link enable
- ASD-ID-002: Windows DPAPI private key protection
- ASD-ID-003: Android Keystore private key (non-exportable)
- ASD-ID-004: Device UUID + public key fingerprint as identity
- ASD-ID-005: IP/MAC/hostname not used as identity

### DISCOVERY
- ASD-DISC-001: mDNS _agentstate._tcp.local advertisement
- ASD-DISC-002: Android NsdManager discovery
- ASD-DISC-003: mDNS contains UUID + display + port + version ONLY
- ASD-DISC-004: No tokens, secrets, or paths in mDNS

### PAIRING
- ASD-PAIR-001: QR code with agentstate:// custom URI scheme
- ASD-PAIR-002: 120-second expiry, single-use
- ASD-PAIR-003: SAS (Short Authentication String) dual confirmation
- ASD-PAIR-004: No auto-binding — both sides must confirm
- ASD-PAIR-005: Pairing session invalidated on failure limit

### CONNECTION
- ASD-CONN-001: TLS 1.3 for all Device Link traffic
- ASD-CONN-002: Desktop self-signed cert, Android fingerprint pinning
- ASD-CONN-003: Challenge-response per session (both directions)
- ASD-CONN-004: Session tokens are ephemeral, not persisted
- ASD-CONN-005: HTTPS + wss only — no plain HTTP
- ASD-CONN-006: Automatic reconnect with exponential backoff
- ASD-CONN-007: IP change does not break binding

### SECURITY
- ASD-SEC-001: Public network → auto-refuse Device Link
- ASD-SEC-002: Device Link Gateway is separate from Core API
- ASD-SEC-003: All restore operations gate through Desktop whitelist
- ASD-SEC-004: HIGH_RISK_WRITE requires Android biometric/PIN
- ASD-SEC-005: Device revocation is immediate
- ASD-SEC-006: Windows Firewall rule: minimal scope only
- ASD-SEC-007: No analytics, no telemetry, no cloud SDKs

### ANDROID UI
- ASD-UI-001: Native Jetpack Compose (not WebView)
- ASD-UI-002: Bottom navigation: Home/Environment/Checkpoints/Changes/Devices/Settings
- ASD-UI-003: Diff viewer (unified default, side-by-side option)
- ASD-UI-004: Restore wizard (checkpoint → coverage → dry-run → confirm → result)
- ASD-UI-005: Dark theme support
- ASD-UI-006: Offline mode indication
- ASD-UI-007: Respects Android Local Network Protection permissions

### CI/CD
- ASD-CI-001: GitHub Actions for Android Gradle build
- ASD-CI-002: GitHub Actions for Tauri Windows build
- ASD-CI-003: APK artifact + installer artifact per release
- ASD-CI-004: Checksums + SBOM per release
