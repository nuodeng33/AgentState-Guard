#!/usr/bin/env python3
"""Parse one or more JUnit XML files and produce a structured test summary.

Usage:
    python3 scripts/parse-test-results.py <path> [<path> ...]

Output: JSON with:
  - tests, passed, failures, errors, skipped, xfailed, xpassed
  - Arithmetic consistency check (tests == passed + failures + errors + skipped)
  - INCONSISTENT_TEST_REPORT if mismatch found
  - ANDROID_UNIT_TESTS_NOT_PRESENT if Gradle NO-SOURCE detected or no test files found
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


GRADLE_NO_SOURCE_PATTERNS = [
    re.compile(r"NO[_-]?SOURCE", re.IGNORECASE),
    re.compile(r"No tests found", re.IGNORECASE),
    re.compile(r"0 tests found", re.IGNORECASE),
    re.compile(r"Tests run: 0,", re.IGNORECASE),
]


def is_gradle_no_source(text: str) -> bool:
    for pat in GRADLE_NO_SOURCE_PATTERNS:
        if pat.search(text):
            return True
    return False


def parse_junit_xml(path: Path) -> dict:
    """Parse a single JUnit XML file and return test counts."""
    tree = ET.parse(path)
    root = tree.getroot()

    result = {
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }

    # JUnit XML: <testsuite tests="..." failures="..." errors="..." skipped="...">
    for testsuite in root.iter("testsuite"):
        tests = int(testsuite.get("tests", 0))
        failures = int(testsuite.get("failures", 0))
        errors = int(testsuite.get("errors", 0))
        skipped = int(testsuite.get("skipped", 0))

        result["tests"] += tests
        result["failures"] += failures
        result["errors"] += errors
        result["skipped"] += skipped

        # Count testcases for detailed breakdown
        for tc in testsuite.iter("testcase"):
            # Check for xfail (expected failure)
            xfail_el = tc.find("skipped")
            if xfail_el is not None and xfail_el.get("message", "").startswith("xfail"):
                result["xfailed"] += 1
                continue

            failure_el = tc.find("failure")
            error_el = tc.find("error")

            if failure_el is not None:
                # Could be xpassed (expected failure but passed)
                if failure_el.get("type") == "xpass":
                    result["xpassed"] += 1
                continue

    # Calculate passed from testcases
    testcase_count = 0
    for testsuite in root.iter("testsuite"):
        for tc in testsuite.iter("testcase"):
            testcase_count += 1
            fail_el = tc.find("failure")
            err_el = tc.find("error")
            skip_el = tc.find("skipped")
            if fail_el is None and err_el is None and skip_el is None:
                result["passed"] += 1

    return result


def check_consistency(result: dict, sources: list) -> dict:
    """Check if test counts are arithmetically consistent.

    Raises SystemExit(1) with INCONSISTENT_TEST_REPORT if:
      - tests != passed + failures + errors + skipped
    """
    total = result["tests"]
    accounted = result["passed"] + result["failures"] + result["errors"] + result["skipped"]

    # xpassed/xfailed are part of totals; add to accounted
    accounted += result["xpassed"] + result["xfailed"]

    if total != accounted and total > 0:
        return {
            "error": "INCONSISTENT_TEST_REPORT",
            "detail": f"tests={total} but passed({result['passed']}) + failures({result['failures']}) + errors({result['errors']}) + skipped({result['skipped']}) + xfailed({result['xfailed']}) + xpassed({result['xpassed']}) = {accounted}",
            "sources": [str(s) for s in sources],
            "result": result,
        }

    return {}


def scan_gradle_results(base_dir: Path) -> dict:
    """Scan for Gradle test results in android build directories."""
    result = {
        "tests": 0,
        "files_found": [],
        "no_source": False,
    }

    # Look for test results XML
    pattern = "**/test-results/**/TEST-*.xml"
    for xml_file in sorted(base_dir.glob(pattern)):
        result["files_found"].append(str(xml_file.relative_to(base_dir)))
        parsed = parse_junit_xml(xml_file)
        result["tests"] += parsed.get("tests", 0)

    # Look for Gradle output indicating NO-SOURCE
    gradle_out = base_dir / "build" / "reports" / "tests"
    if not gradle_out.exists():
        # Check for gradle output files
        for log_file in base_dir.glob("**/test-results/**/gradle*"):
            text = log_file.read_text(errors="replace")
            if is_gradle_no_source(text):
                result["no_source"] = True

    if result["tests"] == 0:
        result["no_source"] = True

    return result


def main():
    paths = [Path(a) for a in sys.argv[1:] if not a.startswith("-")]
    if not paths:
        # Auto-detect: check common locations
        auto_paths = [
            Path("tests"),
            Path("android/app/build/test-results"),
            Path("artifacts"),
        ]
        paths = []
        for ap in auto_paths:
            if ap.exists():
                for f in sorted(ap.rglob("*.xml")):
                    paths.append(f)
        if not paths:
            paths = [Path(".")]

    # Filter to actual files or directories
    xml_files = []
    for p in paths:
        if p.is_file() and p.suffix == ".xml":
            xml_files.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*.xml")):
                xml_files.append(f)

    if not xml_files:
        # Check if this is a Gradle NO-SOURCE scenario
        for p in paths:
            if p.is_dir() and ("gradle" in str(p).lower() or "build" in p.name):
                scan = scan_gradle_results(p)
                if scan["no_source"]:
                    print(
                        json.dumps(
                            {
                                "error": "ANDROID_UNIT_TESTS_NOT_PRESENT",
                                "detail": "No test result files found in expected locations. Gradle may have reported NO-SOURCE.",
                                "sources": [str(p)],
                                "files_scanned": scan["files_found"],
                            },
                            indent=2,
                        )
                    )
                    return 1
                break

        print(json.dumps({"error": "NO_TEST_FILES_FOUND", "sources": [str(p) for p in paths]}, indent=2))
        return 1

    combined = {
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }

    for f in xml_files:
        parsed = parse_junit_xml(f)
        for k in combined:
            combined[k] += parsed.get(k, 0)

    combined["sources"] = [str(f) for f in xml_files]

    # Consistency check
    consistency = check_consistency(combined, xml_files)
    if "error" in consistency:
        print(json.dumps(consistency, indent=2))
        return 1

    # Check for NO-SOURCE
    if combined["tests"] == 0:
        print(
            json.dumps(
                {
                    "error": "ANDROID_UNIT_TESTS_NOT_PRESENT",
                    "detail": "Zero tests found in XML files. No tests were executed.",
                    "sources": [str(f) for f in xml_files],
                },
                indent=2,
            )
        )
        return 1

    print(json.dumps(combined, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
