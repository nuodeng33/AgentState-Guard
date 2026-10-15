---
name: runtime-closure-orchestrator
description: Selects the highest-value next task during runtime closure. Maintains WIP=1 root cause. Blocks new-feature priority over unresolved blockers.
---

# Runtime Closure Orchestrator

## Priority Order

When choosing the next task, select in this order:

1. **Real failing test** — fix the failure first
2. **Runtime blocker** — something that prevents a test from executing (KVM, ADB, NO-SOURCE)
3. **Unwired existing component** — component is built but not connected to the test/CI pipeline
4. **Placeholder data blocking E2E** — missing mock, stub, or config that prevents the end-to-end flow
5. **Stub** — a deliberately simplified version for testing
6. **New feature** — only after all of the above are done

## Blocked Items

The following must NOT be prioritized while items 1–5 exist:
- mDNS / NSD service discovery
- production TLS / certificate management
- QR camera scanning
- System tray integration
- UI polish / animations
- New screens

## WIP = 1 Root Cause

You may work on at most ONE root cause at a time.

**Allowed:**
- Fix root cause A while waiting for CI on root cause A's fix
- Gather evidence for root cause B (read-only) while CI is running for A

**Not allowed:**
- Implementing fixes for two root causes simultaneously
- Starting a new hypothesis while a previous hypothesis is unverified

## Task Boundaries

Every task MUST have:

- **Entry evidence** — what proves this task needs doing (test failure? missing feature? bug report?)
- **Exit condition** — how do we know the task is complete? (specific metric or command output)
- **Verification command** — the exact command(s) to verify the fix
- **CI workflow** — the GitHub Actions workflow that will validate this
- **Blocker condition** — what would block this task and how to detect it

**No exit condition = may not start the task.**

## Context Budget

When context reaches ~75–80%:
1. Stop starting new root causes
2. Update `docs/SESSION_HANDOFF.md` with current state
3. Record active hypotheses, exact commands, run IDs in the handoff
4. Compact or begin a new session

Do not wait until 98% to hand off.
