---
name: android-emulator-forensics
description: Diagnose Android Emulator/ADB/AVD/KVM boot and runtime failures using layered analysis. Never increase timeout as the first response.
---

# Android Emulator Forensics

## Trigger Scope

Invoke for:
- Android Emulator boot failure
- ADB offline
- boot timeout
- empty emulator log
- KVM failure
- AVD creation failure
- APK install failure
- App runtime crash
- Emulator process exited prematurely

## Golden Rule

**NEVER increase timeout as the first response.** Always diagnose the failure layer first.

## Script Usage

```bash
scripts/emulator-probe.sh <avd_name> [timeout_minutes]
```

Or in CI:
```yaml
- name: Probe emulator
  run: |
    scripts/emulator-probe.sh AgentStateGuardTest 15
    cat artifacts/emulator-probe-*/classification.txt
```

## Layer-by-Layer Diagnosis

### Layer 1 — Runner
- OS / CPU architecture
- `/dev/kvm` existence and permissions
- Available RAM and disk

### Layer 2 — SDK
- `sdkmanager` path and version
- `emulator` version
- `platform-tools` version
- Installed system images
- `ANDROID_HOME` and `ANDROID_SDK_ROOT`

### Layer 3 — AVD
- `avdmanager` result
- `config.ini` (disk size, RAM, image architecture)
- Snapshot state

### Layer 4 — Emulator Process
- Exact command used to start
- PID alive status
- Exit code (if exited)
- stdout / stderr content
- Duration before exit

### Layer 5 — ADB
- `adb server` state
- `adb devices -l` output
- Device state: device / offline / unauthorized / no device
- `adb get-state`

### Layer 6 — Android Boot
- `sys.boot_completed` property
- `init.svc.bootanim` (running/stopped)
- Package manager readiness
- SurfaceFlinger / activity manager state

## Failure Classification

Output ONE of these:

| Classification | Condition |
|---------------|-----------|
| `RUNNER_KVM_FAILURE` | `/dev/kvm` missing or inaccessible |
| `SDK_COMPONENT_MISSING` | System image / platform / build-tools not installed |
| `AVD_CONFIGURATION_FAILURE` | AVD creation failed or config incompatible |
| `EMULATOR_PROCESS_EXITED` | Emulator PID died before boot |
| `ADB_OFFLINE` | ADB can't reach emulator (offline/unauthorized) |
| `ANDROID_BOOT_STALLED` | Boot completed never reached after 15+ min (process alive, ADB connected) |
| `APK_INSTALL_FAILURE` | `adb install` failed |
| `APP_RUNTIME_CRASH` | App launched but crashed at runtime |
| `UNKNOWN_WITH_EVIDENCE` | Not classified above — record all evidence |

**NEVER output `EMULATOR_BLOCKED` or `BOOT_TIMEOUT` without specifying the failure layer.**

## Policy

Only allow increased timeout if evidence shows:
- Process is still alive
- ADB state is "device" and progressing
- `sys.boot_completed` has not yet been set but `bootanim` is running
- (i.e., the system is still booting, just slow)
