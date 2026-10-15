#!/usr/bin/env python3
"""Run all engineering skill pack validation fixtures.

Usage:
    python3 fixtures/run-all-fixtures.py

Each fixture is independent. Continues on failure.
Exits non-zero if any fixture fails.
"""

import json
import subprocess
import sys
from pathlib import Path


FIXTURES = []


def fixture(name: callable):
    FIXTURES.append((name.__name__, name))
    return name


def log(msg):
    print(f"  {msg}")


def run_script(*args, **kwargs):
    result = subprocess.run(*args, capture_output=True, text=True, **kwargs)
    return result


# ── Fixture A: Inconsistent JUnit ───────────────────────────────
@fixture
def fixture_a_inconsistent_junit():
    """JUnit XML says tests=226 but a separate line shows 227 passed.
    Script must reject."""
    result = run_script(
        [sys.executable, "scripts/parse-test-results.py", "fixtures/junit-inconsistent.xml"],
        cwd=Path(__file__).resolve().parent.parent,
    )
    output = json.loads(result.stdout) if result.stdout else {}
    if result.returncode == 0:
        log("FAIL: Script should have rejected inconsistent tally")
        return False
    if output.get("error") == "INCONSISTENT_TEST_REPORT":
        log("PASS: INCONSISTENT_TEST_REPORT detected")
        return True
    log(f"FAIL: Unexpected error type: {output.get('error', 'none')}")
    return False


# ── Fixture B: Gradle NO-SOURCE ─────────────────────────────────
@fixture
def fixture_b_gradle_no_source():
    """Gradle output says NO-SOURCE. Script must detect no tests ran."""
    from scripts.parse_test_results import is_gradle_no_source

    if is_gradle_no_source("NO-SOURCE"):
        log("PASS: NO-SOURCE detected in string")
    else:
        log("FAIL: NO-SOURCE not detected in string")
        return False

    if is_gradle_no_source("No tests found for given"):
        log("PASS: 'No tests found' detected")
    else:
        log("FAIL: 'No tests found' not detected")
        return False

    # Test that Gradle output file triggers detection via inline function
    from scripts.parse_test_results import is_gradle_no_source
    with open("fixtures/gradle-no-source.txt") as f:
        content = f.read()
    if is_gradle_no_source(content):
        log("PASS: ANDROID_UNIT_TESTS_NOT_PRESENT detected from file content")
    else:
        log("FAIL: ANDROID_UNIT_TESTS_NOT_PRESENT not detected from file")
        return False

    # Also verify via script looking at empty dir (no XML files = no tests)
    result = run_script(
        [sys.executable, "scripts/parse-test-results.py", "fixtures/gradle-empty"],
        cwd=Path(__file__).resolve().parent.parent,
    )
    if "NO_TEST_FILES_FOUND" in result.stdout or "ANDROID_UNIT_TESTS_NOT_PRESENT" in result.stdout:
        log("PASS: Script correctly reports no test files in empty dir")
        return True
    log(f"FAIL: Unexpected: {result.stdout[:200]}")
    return False


# ── Fixture C: Retry Guard — 3 identical failures ────────────────
@fixture
def fixture_c_retry_guard_triple_failure():
    """Simulate 3 identical failed commands. Third must be blocked."""
    import tempfile

    from scripts.retry_guard import (
        fingerprint_command,
        is_always_allowed,
        pre_tool_use,
        post_tool_use,
    )

    # Use a temp state file
    from scripts.retry_guard import STATE_FILE

    original_state = STATE_FILE

    with tempfile.TemporaryDirectory() as tmpdir:
        test_state = Path(tmpdir) / "retry-guard-state.json"

        # Monkey-patch state file
        import scripts.retry_guard as rg

        rg.STATE_FILE = test_state

        try:
            cmd = "python3 -m pytest tests/ -v --tb=long"

            # First call
            r1 = pre_tool_use("Bash", cmd)
            assert r1["decision"] == "ALLOW", f"First call should ALLOW: {r1}"
            log("PASS: First identical failure allowed")

            # Record first failure
            post_tool_use("Bash", cmd, "1", "test error")

            # Second call
            r2 = pre_tool_use("Bash", cmd)
            assert r2["decision"] == "ALLOW", f"Second call should ALLOW: {r2}"
            log("PASS: Second identical failure allowed (with warning)")

            # Record second failure
            post_tool_use("Bash", cmd, "1", "test error")

            # Third call — should BLOCK
            r3 = pre_tool_use("Bash", cmd)
            if r3["decision"] == "BLOCK":
                log(f"PASS: Third identical failure BLOCKED: {r3['reason']}")
            else:
                log(f"FAIL: Third call should BLOCK, got: {r3['decision']}")
                return False

            # Verify success resets
            post_tool_use("Bash", cmd, "0", "")
            r4 = pre_tool_use("Bash", cmd)
            assert r4["decision"] == "ALLOW", f"After success should ALLOW: {r4}"
            log("PASS: After success, failure count reset")

            return True

        finally:
            rg.STATE_FILE = original_state


# ── Fixture D: Emulator PID exit ─────────────────────────────────
@fixture
def fixture_d_emulator_pid_exit():
    """Emulator PID exits — probe must detect within 60s, not wait 15min."""
    result = subprocess.run(
        [sys.executable, "fixtures/fixture_emulator_pid_exit.py"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    print(result.stdout)
    if result.returncode == 0:
        log("PASS: Emulator PID exit detection verified")
        return True
    log(f"FAIL: Emulator PID exit test exited {result.returncode}")
    log(f"stderr: {result.stderr[:200]}")
    return False


# ── Fixture E: Golden Vector Drift ──────────────────────────────
@fixture
def fixture_e_golden_vector_drift():
    """Simulate cross-language divergence — must locate first differing byte,
    not just change assertion."""
    from agentguard.device_link.crypto import build_pairing_transcript
    from tests.reference_crypto import ref_build_pairing_transcript, TRANSCRIPT_A_PARAMS

    # Verify both implementations produce the same output for the same input
    prod = build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
    ref = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)

    if prod == ref:
        log("PASS: Production and reference transcripts match")
    else:
        # Find first differing byte
        for i, (a, b) in enumerate(zip(prod, ref)):
            if a != b:
                log(f"First divergence at byte {i}: prod={a:#04x} ref={b:#04x}")
                break
        log("FAIL: Production and reference transcripts differ")
        return False

    # Verify reference does not import production
    import tests.reference_crypto as rc
    src_file = rc.__file__
    with open(src_file) as f:
        content = f.read()
    if "import agentguard" in content or "from agentguard" in content:
        log("FAIL: Reference crypto imports agentguard")
        return False
    log("PASS: Reference crypto is independent")

    return True


# ── Main ─────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Engineering Skill Pack — Validation Fixtures")
    print("=" * 60)

    # Need to add project root to path
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    results = []
    passed = 0
    failed = 0

    for name, fn in FIXTURES:
        print(f"\n--- {name} ---")
        try:
            ok = fn()
        except Exception as e:
            import traceback
            log(f"EXCEPTION: {e}")
            traceback.print_exc()
            ok = False

        if ok:
            passed += 1
            results.append((name, "PASS"))
        else:
            failed += 1
            results.append((name, "FAIL"))

    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)
    for name, status in results:
        print(f"  {status:4}  {name}")
    print("-" * 60)
    print(f"  {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
