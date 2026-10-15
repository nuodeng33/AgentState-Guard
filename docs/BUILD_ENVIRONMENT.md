# Build Environment — AgentState Guard

## Isolation Rule
AgentSandbox (`D:\AgentSandbox`) is STABLE. Do NOT install toolchains there.
All build toolchains go to `D:\AgentBuild\`.

## Directory Layout
```
D:\AgentBuild\
  toolchains\        # Rust, JDK, etc.
  android-sdk\       # ANDROID_HOME
  gradle\            # GRADLE_USER_HOME
  cargo\             # CARGO_HOME
  artifacts\         # build outputs
  logs\              # build logs
```

## Setup Order
1. `.\scripts\env-doctor.ps1`          — diagnose what's missing
2. `.\scripts\setup-windows-build.ps1` — Rust + Tauri CLI + MSVC + WebView2
3. `.\scripts\setup-android-sdk.ps1`   — JDK + Android SDK + platform tools
4. `.\scripts\build-desktop.ps1`       — compile Windows .exe
5. `.\scripts\build-android.ps1`       — compile debug APK
6. `.\scripts\test-desktop.ps1`        — smoke test the running .exe

## Current Status (headless Debian)
| Platform | Status |
|----------|--------|
| Python core (Linux) | HEADLESS_VERIFIED |
| Tauri desktop (Windows) | SOURCE_IMPLEMENTED — needs Windows toolchain |
| Android APK | SOURCE_IMPLEMENTED — needs Android SDK |

## Uninstall
- Rust: `rustup self uninstall`
- Android SDK: delete `ANDROID_HOME` directory
- MSVC: via Windows "Add or remove programs"
- Project caches: delete `D:\AgentBuild\`
