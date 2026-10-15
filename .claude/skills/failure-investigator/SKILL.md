---
name: failure-investigator
description: Must be invoked for ANY test/build/CI/runtime failure before proposing fixes. Enforces evidence-first debugging with 6 phases, debug session ledger, and leverages systematic-debugging.
---

# Failure Investigator

## Trigger Scope

Invoke this skill when encountering:
- test failure
- CI failure
- build failure
- runtime crash
- timeout
- unexpected output
- repeated failed command
- integration mismatch

## Phase 1 — Evidence (MANDATORY before any fix)

Record ALL of the following BEFORE changing any code:

- **Exact failing command** (copy-paste, don't paraphrase)
- **Exact exit code / error signal**
- **Complete relevant error output** (first failure + full traceback)
- **Run ID** (CI run number, workflow name, attempt)
- **Commit** (HEAD hash, branch)
- **Environment** (OS, Python version, Java version, SDK versions)
- **First failing layer** (Python test? Gradle build? Emulator boot? HTTP?)
- **Whether reproducible** (run the same command again locally)
- **Recent relevant diff** (`git diff HEAD~1` or the PR's diff range)

Output to: `artifacts/debug/<issue-id>/diagnosis.md` (see ledger format below)

> **Precondition for Phase 2:** Phase 1 must be complete. If you don't have the evidence, you may not continue.

## Phase 2 — Root Cause Trace

Answer each question in sequence. Do not skip any.

1. **At which layer does the error first appear?** (test → lib → transport → protocol → crypto)
2. **What data enters this layer?** (inputs, parameters, environment)
3. **What is the actual output vs expected output?**
4. **Who produced the erroneous input for this layer?** (trace backward)
5. **What is the difference between a working example and the failing example?** (diff inputs, config, environment, call path)

Document findings in the diagnosis ledger.

## Phase 3 — Single Hypothesis

Write exactly one sentence:

```
I believe <root cause statement> because <evidence summary>.
```

Only ONE hypothesis at a time. No fix attempts without a stated hypothesis.

## Phase 4 — Minimal Experiment

Design the smallest possible experiment to confirm or disprove your hypothesis.

**Never propose a direct commit of a "possible fix" at this stage.** Instead:
- Add a debug print/log
- Run with modified input
- Isolate the component
- Use a standalone test script

Only proceed to Phase 5 if the experiment confirms the hypothesis.

## Phase 5 — Fix

Only after the hypothesis is confirmed by experiment evidence, modify production code.

- Make the minimum change that addresses the *root cause*, not the symptom
- Do not refactor unrelated code
- Do not "fix" things not part of the causal chain

## Phase 6 — Verification

1. Targeted test of the failing case
2. Full relevant test file
3. Complete test suite
4. GitHub CI (if applicable)

## Relationship to systematic-debugging

This skill does NOT replace `systematic-debugging`. It is a specialized override for CI/build/runtime failures that additionally enforces:
- Evidence recording in a durable ledger
- Layer-by-layer backward trace
- Prevention of fix proposals without completed Phase 1

If this skill's phases conflict with `systematic-debugging`, use this skill's phases (they are stricter for CI context).

If a general-purpose debug session is in progress, invoke `systematic-debugging` instead.

## Diagnosis Ledger Format

Every activation maintains a file at `artifacts/debug/<issue-id>/diagnosis.md`:

```markdown
# Failure

**Command:** `...`
**Exit code:** ...
**Commit:** ...
**CI run:** ...
**Environment:** ...

# Reproduction

**Steps:**
**Reproducible:** yes/no
**Frequency:** always/intermittent/once

# Evidence

**Error:** (relevant excerpt, not full dump)
**Logs:** (log file location + key excerpt)
**First failing component:** ...
**Recent changes:** (git diff HEAD~1 summary)

# Hypothesis N

**Statement:** I believe ... because ...
**Supporting evidence:** ...
**Disproving evidence:** ...
**Minimal experiment:** ...
**Result:** confirmed/disproven

# Root Cause

**Confirmed:** ...
**Evidence:** ...

# Fix

**Files changed:** ...
**Why this fixes the source:** ...

# Verification

**Targeted:** ...
**Suite:** ...
**CI:** ...
```

Security: Do NOT write secrets or full credentials into the ledger. Redact any suspected credential values with `<REDACTED>`.
