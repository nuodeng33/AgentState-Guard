# Toolchain Version Matrix — AgentState Guard

## Python Core
| Tool | Min Version | Notes |
|------|------------|-------|
| Python | 3.11+ | Python 3.13 supported |
| pip | 23+ | |
| pytest | 9+ | dev dependency |
| cryptography | 41+ | ECDSA P-256 |
| fastapi | 0.115+ | API server |
| uvicorn | 0.34+ | HTTP server |

## Desktop (Tauri 2)
| Tool | Version | Notes |
|------|---------|-------|
| Rust (stable) | 1.77+ | edition = 2021 |
| Cargo | bundled with Rust | |
| Tauri CLI | 2.x | `cargo install tauri-cli` |
| Microsoft C++ Build Tools | VS 2022 | Desktop development with C++ |
| Windows SDK | 10.0.22621+ | Win11 SDK or newer |
| WebView2 | evergreen | Pre-installed on Win11 |
| Node.js | 18+ | for Vite frontend build |
| npm | 9+ | |

## Android
| Tool | Version | Notes |
|------|---------|-------|
| JDK | 17 (Temurin) | Required by AGP 8.x |
| Gradle | 8.7+ | Via wrapper |
| Android SDK Platform | 34+ | compileSdk |
| Android Build Tools | 34.0.0+ | |
| Android Command-Line Tools | latest | sdkmanager |
| Kotlin | 2.0+ | Compose compiler |
| AGP (Android Gradle Plugin) | 8.5+ | |
| Min SDK | 26 (Android 8) | |
| Target SDK | 34+ | |
| Compile SDK | 34+ | |

## Disk Estimates
| Component | Size |
|-----------|------|
| Rust + Cargo | ~2 GB |
| MSVC Build Tools | ~4 GB |
| Android SDK (minimal) | ~1.5 GB |
| JDK 17 | ~300 MB |
| Gradle cache | ~500 MB |
| **Total est.** | **~8 GB** |
