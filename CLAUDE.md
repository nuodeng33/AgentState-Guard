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

## Compact Instructions
When compacted, preserve: product goal, current phase, active requirement IDs, security boundaries, decisions, files changed, test results, Git HEAD, next action.

After compaction, read CURRENT_STATE.md, SESSION_HANDOFF.md, ACCEPTANCE_MATRIX.md, and active phase contract before continuing.
