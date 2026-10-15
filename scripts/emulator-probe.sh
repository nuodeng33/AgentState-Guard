#!/usr/bin/env bash
# Emulator Probe Script — structured output every 20-30s.
# Saves: emulator stdout/stderr, ADB state, AVD config.
# Exits immediately if PID disappears (no 15-min wait).
#
# Usage: scripts/emulator-probe.sh <avd_name> [timeout_minutes]
#   Default timeout: 15 minutes
set -euo pipefail

AVD_NAME="${1:?"Usage: $0 <avd_name> [timeout_minutes]"}"
MAX_MINUTES="${2:-15}"
MAX_SECONDS=$((MAX_MINUTES * 60))
START_TS=$(date +%s)
PROBE_DIR="artifacts/emulator-probe-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$PROBE_DIR"

# ── Layer 1: Runner ─────────────────────────────────────────────
{
    echo "=== Layer 1: Runner ==="
    echo "OS: $(uname -a 2>/dev/null || echo 'N/A')"
    echo "CPU: $(lscpu 2>/dev/null | grep 'Model name' | head -1 || echo 'N/A')"
    echo "Arch: $(uname -m 2>/dev/null || echo 'N/A')"
    if [ -e /dev/kvm ]; then
        echo "KVM: present"
        ls -la /dev/kvm 2>/dev/null
    else
        echo "KVM: NOT_FOUND"
    fi
    echo "RAM: $(free -h 2>/dev/null | grep Mem | awk '{print $2}' || echo 'N/A')"
    echo "Disk: $(df -h / 2>/dev/null | tail -1 | awk '{print $4}' || echo 'N/A')"
} > "$PROBE_DIR/layer1-runner.txt"

# ── Layer 2: SDK ────────────────────────────────────────────────
{
    echo "=== Layer 2: SDK ==="
    echo "ANDROID_HOME: ${ANDROID_HOME:-NOT_SET}"
    echo "ANDROID_SDK_ROOT: ${ANDROID_SDK_ROOT:-NOT_SET}"
    if command -v sdkmanager &>/dev/null; then
        echo "sdkmanager: $(which sdkmanager)"
        sdkmanager --list --verbose 2>/dev/null | grep -E "system-images|platforms;android|build-tools" | head -20 || true
    fi
    if command -v emulator &>/dev/null; then
        echo "emulator: $(which emulator)"
        emulator -version 2>/dev/null || echo "emulator version: N/A"
    fi
    if command -v adb &>/dev/null; then
        echo "adb: $(which adb)"
        adb version 2>/dev/null || true
    fi
} > "$PROBE_DIR/layer2-sdk.txt"

# ── Layer 3: AVD config ─────────────────────────────────────────
AVD_DIR="${ANDROID_HOME:-$HOME/android-sdk}"
for d in "$AVD_DIR"/.android/avd/ "$HOME/.android/avd/"; do
    if [ -d "$d" ]; then
        echo "AVD dir: $d"
        ls -la "$d" 2>/dev/null
        for ini in "$d"/*.ini "$d"/*/config.ini; do
            [ -f "$ini" ] && echo "--- $ini ---" && cat "$ini" 2>/dev/null
        done
        break
    fi
done > "$PROBE_DIR/layer3-avd.txt" 2>/dev/null || true

echo "AVD name: $AVD_NAME" >> "$PROBE_DIR/layer3-avd.txt"

# ── Start Emulator ─────────────────────────────────────────────
EMULATOR_CMD="${EMULATOR:-emulator}"
echo "Starting emulator: $EMULATOR_CMD -avd $AVD_NAME -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -memory 2048 -netdelay none -netspeed full -no-snapshot -verbose"

# Redirect stdout to separate log, stderr too
nohup "$EMULATOR_CMD" \
    -avd "$AVD_NAME" \
    -no-window -no-audio -no-boot-anim \
    -gpu swiftshader_indirect \
    -memory 2048 \
    -netdelay none -netspeed full \
    -no-snapshot \
    -verbose \
    > "$PROBE_DIR/emulator-stdout.log" 2>"$PROBE_DIR/emulator-stderr.log" &
EMULATOR_PID=$!
echo "EMULATOR_PID=$EMULATOR_PID" | tee -a "$PROBE_DIR/layer4-process.txt"

sleep 5  # Initial wait for process startup

# ── Layer 4 & 5: Process & ADB monitoring ──────────────────────
echo "timestamp,pid_alive,adb_state,boot_completed,bootanim,elapsed_seconds" > "$PROBE_DIR/probe-timeseries.csv"

ELAPSED=0
while [ $ELAPSED -lt $MAX_SECONDS ]; do
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    PID_ALIVE="YES"
    kill -0 "$EMULATOR_PID" 2>/dev/null || PID_ALIVE="NO"

    # ADB state
    ADB_STATE="N/A"
    BOOT_COMPLETED="N/A"
    BOOTANIM="N/A"

    if [ "$PID_ALIVE" = "NO" ]; then
        echo "EMULATOR_PROCESS_EXITED: PID $EMULATOR_PID gone at ${ELAPSED}s"
        # Check exit code
        wait "$EMULATOR_PID" 2>/dev/null
        EXIT_CODE=$?
        echo "exit_code=$EXIT_CODE" >> "$PROBE_DIR/layer4-process.txt"
        break
    fi

    # ADB
    if command -v adb &>/dev/null; then
        ADB_STATE=$(adb get-state 2>/dev/null | tr -d '\r' || echo "OFFLINE")
        BOOT_COMPLETED=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || echo "N/A")
        BOOTANIM=$(adb shell getprop init.svc.bootanim 2>/dev/null | tr -d '\r' || echo "N/A")
    fi

    echo "$NOW,$PID_ALIVE,$ADB_STATE,$BOOT_COMPLETED,$BOOTANIM,$ELAPSED" >> "$PROBE_DIR/probe-timeseries.csv"
    echo "[${ELAPSED}s] pid=$PID_ALIVE adb=$ADB_STATE boot=$BOOT_COMPLETED anim=$BOOTANIM"

    # Check boot completed
    if [ "$ADB_STATE" = "device" ] && [ "$BOOT_COMPLETED" = "1" ]; then
        echo "BOOT_COMPLETED after ~${ELAPSED}s" | tee -a "$PROBE_DIR/boot-result.txt"
        # Save ADB devices list
        adb devices -l 2>/dev/null > "$PROBE_DIR/layer5-adb.txt" || true
        break
    fi

    sleep 20
    ELAPSED=$((ELAPSED + 20))
done

# ── Final state ─────────────────────────────────────────────────
kill -0 "$EMULATOR_PID" 2>/dev/null && echo "Process still alive after ${ELAPSED}s" >> "$PROBE_DIR/boot-result.txt" || echo "Process exited" >> "$PROBE_DIR/boot-result.txt"
adb devices -l 2>/dev/null > "$PROBE_DIR/layer5-adb-final.txt" || true

if [ $ELAPSED -ge $MAX_SECONDS ]; then
    echo "BOOT_TIMEOUT after ${MAX_MINUTES} minutes" | tee -a "$PROBE_DIR/boot-result.txt"
    # Classification
    if [ ! -e /dev/kvm ]; then
        echo "CLASSIFICATION=RUNNER_KVM_FAILURE" >> "$PROBE_DIR/classification.txt"
    elif ! adb get-state 2>/dev/null | grep -q "device"; then
        echo "CLASSIFICATION=ADB_OFFLINE" >> "$PROBE_DIR/classification.txt"
    else
        echo "CLASSIFICATION=ANDROID_BOOT_STALLED" >> "$PROBE_DIR/classification.txt"
    fi
else
    echo "CLASSIFICATION=BOOT_SUCCESSFUL" >> "$PROBE_DIR/classification.txt"
fi

echo "Probe artifacts saved to: $PROBE_DIR"
