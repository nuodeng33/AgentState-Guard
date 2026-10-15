---
name: ci-evidence-auditor
description: Parses JUnit XML / Gradle test results / CI metadata to extract verified test counts. Detects INCONSISTENT_TEST_REPORT, NO-SOURCE, and artifact verification failures.
---

# CI Evidence Auditor

## Trigger Scope

Invoke when working with:
- pytest results
- JUnit XML
- Gradle test output (including NO-SOURCE)
- GitHub Actions workflow results
- skipped/xfail counts
- build artifacts
- conflicting test totals (e.g., "226 collected" vs "227 passed")
- false green (no tests actually ran)

## Mandatory Rule

**NEVER manually add test counts from different CI runs.** Always use a deterministic parser.

## Script Usage

```bash
python3 scripts/parse-test-results.py <path-to-junit-xml...>
```

The script auto-detects:
- JUnit XML in `tests/`
- Gradle test results in `android/app/build/test-results/`
- Inconsistencies (tests != passed + failures + errors + skipped)

## Classification & Actions

### INCONSISTENT_TEST_REPORT
If `tests != passed + failures + errors + skipped`, the script exits non-zero with:
```json
{"error": "INCONSISTENT_TEST_REPORT", ...}
```
**Action:** Do NOT use these numbers. Identify the correct test result source.

### ANDROID_UNIT_TESTS_NOT_PRESENT
If Gradle reports NO-SOURCE or zero test XML files found:
```json
{"error": "ANDROID_UNIT_TESTS_NOT_PRESENT", ...}
```
**Action:** This means **no tests actually ran**. Do NOT mark ANDROID_UNIT_TESTS_VERIFIED.

### Consistent report
Normal output:
```json
{"tests": ..., "passed": ..., "failures": ..., "errors": ..., "skipped": ...}
```
Arithmetic validated: `tests == passed + failures + errors + skipped`

## Artifact Verification

After CI build artifacts:

### Windows (.exe)
- File must exist at the expected path
- Size > 0
- Where practical: check PE magic bytes (`MZ` at offset 0)

### Android (.apk)
- File must exist
- Size > 0
- Package metadata readable via `aapt` or `unzip -p AndroidManifest.xml`

**Never** equate "workflow success" with "artifact verified." Always check file existence and integrity.

## NO-SOURCE Detection

When reviewing Gradle output:
1. Search for `NO-SOURCE` in the output
2. Check `android/app/build/test-results/` for actual XML files
3. If no XML files exist, it is `ANDROID_UNIT_TESTS_NOT_PRESENT`
4. If XML files exist but have 0 tests, it is also `ANDROID_UNIT_TESTS_NOT_PRESENT`
