# AgentState Guard

## Source of Truth
Before changing code, read:
1. docs/spec/MASTER_SPEC.md
2. docs/spec/REQUIREMENTS.md
3. docs/spec/ACCEPTANCE_MATRIX.md
4. docs/CURRENT_STATE.md
5. Current phase contract under docs/phases/

Do not silently change requirements.

## Phase Protocol
Every phase must have: requirement IDs, scope, non-goals, affected files, risks, tests, rollback point, exit criteria.

At phase completion, update: ACCEPTANCE_MATRIX.md, CURRENT_STATE.md, SESSION_HANDOFF.md, ROLLBACK_LEDGER.md, IMPLEMENTATION_LOG.md.

## Safety
- Do not expose secrets.
- Do not modify files outside this project.
- Do not push or create remote resources.
- Do not install global packages.
- Do not claim complete without fresh verification evidence.

## Permission Freeze
Claude Code permissions must not be modified during v1.0 development. If a permission is insufficient, record in docs/ENVIRONMENT_GAPS.md and continue with other tasks.

## Skill Routing Rules

When encountering the following scenarios, invoke the corresponding skill FIRST:

### Failure Investigation (mandatory before any fix)
- ANY test/build/CI/runtime failure → invoke `failure-investigator`
- Same failure attempted 3 times → stop and invoke `failure-investigator`
- This enforces evidence collection before fix proposals

### CI Evidence Audit
- Parsing JUnit/Gradle/CI test results → invoke `ci-evidence-auditor`
- Verifying test counts, NO-SOURCE, or artifact integrity → invoke `ci-evidence-auditor`
- Never manually sum counts from different runs

### Android Emulator Diagnostics
- Emulator boot/ADB/KVM/AVD failure → invoke `android-emulator-forensics`
- Never increase timeout as first response

### Contract Drift
- Python/Kotlin divergence, golden vector mismatch, protocol migration → invoke `contract-drift-debugger`
- Never change assertions just to make tests pass

### Task Prioritization
- Choosing next engineering task → invoke `runtime-closure-orchestrator`

### Context Budget
- Context ~75-80% → invoke `strategic-compact` or `context-compression`, update SESSION_HANDOFF.md

## Mandatory Workflow: Every Feature & Bug Fix

Every new requirement or bug fix MUST follow this 5-step pipeline. No step may be skipped.

### 1. Intent → TODO
- Parse the request into a concrete task list using `TaskCreate`.
- Each task must have: `subject`, `description`, `activeForm`.
- Mark the current task `in_progress` before beginning any work.

### 2. Design → Sub-agent
- Spawn a sub-agent (`Agent` tool with `subagent_type: "general-purpose"` or `"Plan"`) to produce a design plan.
- The sub-agent receives: the exact requirement text, relevant file paths, and constraint checklist.
- Sub-agent output: affected files, data flow, edge cases, risk assessment, test strategy.
- Sub-agent runs in background unless its output blocks the next step.

### 3. Review → Gate Check
- Read the sub-agent's plan.
- Gate criteria:
  - Does the plan address every requirement item?
  - Are file paths scoped to this project only?
  - Is there any security escalation or permission widening?
  - Is rollback explicitly defined?
- If plan fails gate → return to step 2 with corrections.

### 4. Implement → Code
- Follow the approved plan exactly. Do not drift.
- Write tests alongside implementation (TDD: red → green).
- Do not refactor unrelated modules.
- Do not add features beyond the requirements.

### 5. Verify → Sub-agent Tests
- Spawn a sub-agent to run the full test suite and report results.
- Sub-agent receives: the list of changed files and the test command.
- Sub-agent output: pass/fail count, first failure details, coverage gap notes.
- If any test fails → fix before marking task complete.
- Mark task `completed` only after all tests pass.

### Task Completion Checklist
- [ ] All TaskCreate tasks marked `completed`
- [ ] Git status clean
- [ ] `docs/spec/ACCEPTANCE_MATRIX.md` updated
- [ ] `docs/CURRENT_STATE.md` updated
- [ ] `docs/SESSION_HANDOFF.md` updated
- [ ] Atomic commit with requirement ID in message

## Compact Instructions
When compacted, preserve: product goal, current phase, active requirement IDs, security boundaries, decisions, files changed, test results, Git HEAD, next action.

After compaction, read CURRENT_STATE.md, SESSION_HANDOFF.md, ACCEPTANCE_MATRIX.md, and active phase contract before continuing.

## Skill Assessment (2026-07-25)
- 5 project skills (.claude/skills/) + superpowers (systematic-debugging, TDD, verification) + built-in (strategic-compact, context-compression) → sufficient for Runtime Closure
- Marketplace skills (code-review, claude-md-improver, claude-security) deferred to PR phase
- No new marketplace plugins enabled during Core CI + Android Runtime phase
