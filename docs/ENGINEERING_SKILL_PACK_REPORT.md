# Engineering Skill Pack — Installation Report

## Existing Skills

| Skill | Source | Location | Discoverable | Actual Trigger |
|-------|--------|----------|-------------|----------------|
| systematic-debugging | superpowers plugin | `~/.claude/plugins/cache/superpowers/skills/systematic-debugging/SKILL.md` | Yes (plugin) | No — not routed in CLAUDE.md |
| test-driven-development | superpowers plugin | Same plugin | Yes | No — not routed |
| verification-before-completion | superpowers plugin | Same plugin | Yes | No — not routed |
| brainstorming | superpowers plugin | Same plugin | Yes | No — not routed |
| writing-skills | superpowers plugin | Same plugin | Yes | Marketed as superpowers |
| context-compression | built-in | `~/.claude/skills/context-compression/` | Yes | Via hook on Edit/Write |
| filesystem-context | built-in | `~/.claude/skills/filesystem-context/` | Yes | Manual only |
| strategic-compact | built-in | `~/.claude/skills/strategic-compact/` | Yes | Via hook on Edit/Write |

## Root Cause of Blind Retry

Three concrete mechanisms caused the blind-retry pattern:

1. **Claude.md did not route failures to systematic-debugging.** The skill existed in the superpowers cache but there was no instruction saying "when a test fails, call this skill first." Without routing rules, the model fell back to general-purpose behavior.

2. **No retry guard existed.** The only hooks were `PostToolUse` on Edit/Write (for strategic-compact). No hook monitored repeated `Bash` command failures. The model could attempt the same failing command indefinitely because nothing tracked attempt counts.

3. **No project-level skills for CI/emulator/contract drift.** The superpowers' systematic-debugging is general-purpose. CI, emulator, and contract-drift failures need specialized layered diagnosis (OS → SDK → AVD → process → ADB → boot). The superpowers skill didn't provide this structure, and the model had no reason to build it.

## Installed Skills

### failure-investigator
| Property | Value |
|----------|-------|
| Path | `.claude/skills/failure-investigator/SKILL.md` |
| Trigger | Any test/build/CI/runtime failure |
| Phases | Evidence → Root Cause → Hypothesis → Experiment → Fix → Verify |
| Ledger | `artifacts/debug/<issue-id>/diagnosis.md` |
| Relation | References/delegates to systematic-debugging |

### ci-evidence-auditor
| Property | Value |
|----------|-------|
| Path | `.claude/skills/ci-evidence-auditor/SKILL.md` |
| Trigger | JUnit XML, Gradle results, CI metadata, artifact verification |
| Deterministic script | `scripts/parse-test-results.py` |
| Features | INCONSISTENT_TEST_REPORT, ANDROID_UNIT_TESTS_NOT_PRESENT |

### android-emulator-forensics
| Property | Value |
|----------|-------|
| Path | `.claude/skills/android-emulator-forensics/SKILL.md` |
| Trigger | Emulator boot/ADB/AVD/KVM/install/runtime failures |
| Layers | Runner → SDK → AVD → Process → ADB → Boot |
| Probe script | `scripts/emulator-probe.sh` |
| Classification | RUNNER_KVM_FAILURE through UNKNOWN_WITH_EVIDENCE |

### contract-drift-debugger
| Property | Value |
|----------|-------|
| Path | `.claude/skills/contract-drift-debugger/SKILL.md` |
| Trigger | API drift, Python/Kotlin mismatch, golden vector drift |
| 4-way comparison | Spec × Implementation × Tests × Consumer |
| Classification | A (test obsolete) through E (reference drift) |
| Byte-level debug | Locate first divergent byte before comparing final output |

### runtime-closure-orchestrator
| Property | Value |
|----------|-------|
| Path | `.claude/skills/runtime-closure-orchestrator/SKILL.md` |
| Trigger | Task prioritization |
| Priority | Real failure > runtime blocker > unwired component > stub > new feature |
| WIP limit | 1 root cause |
| Context budget | Hand off at 75-80% |

## Retry Guard

| Property | Value |
|----------|-------|
| Hook type | `PreToolUse` + `PostToolUse` on `Bash` |
| State file | `.claude/retry-guard-state.json` |
| Blocking rule | 3 identical failures with unchanged HEAD + working tree + diagnosis ledger |
| False-positive protection | Always-allows: git status/diff/log, gh run/view, targeted pytest, read-only inspection |
| Reset conditions | Success exit code, code change, diagnosis ledger update, user-requested rerun |
| Redaction | Command arguments redact tokens, API keys, auth headers |
| Security | Does not read credentials, does not output environment variables |

## Fixture Validation Results

| Fixture | Description | Result |
|---------|-------------|--------|
| A | Inconsistent JUnit (226 tests / 0 passed) | ✅ PASS |
| B | Gradle NO-SOURCE detection | ✅ PASS |
| C | Retry guard blocks 3rd identical failure | ✅ PASS |
| D | Emulator PID exit detection | ✅ PASS |
| E | Golden vector matching + reference independence | ✅ PASS |

## New Session Discovery

All 5 project skills are discoverable via the `.claude/skills/` directory structure. Claude Code v2.1.217 automatically discovers skills at:
- `~/.claude/skills/*/SKILL.md` (user-level, built-in skills)
- `~/.claude/plugins/*/skills/*/SKILL.md` (plugin skills)
- `PROJECT/.claude/skills/*/SKILL.md` (project skills — this installation)

Skills are listed in the system reminder at session start when the project's `.claude/skills/` directory contains valid SKILL.md files with proper frontmatter (`name` and `description` fields).

## Security Audit

| Check | Result |
|-------|--------|
| Credentials read from config files | NO |
| Credentials in skill/fixture/script files | NO (retry-guard has redaction patterns only) |
| Environment variables leaked | NO |
| Product code modified | NO |
| Third-party executables installed | NO |
| Global packages installed | NO |

## Git

| Property | Value |
|----------|-------|
| Branch | feat/device-link |
| Commit message | Add evidence-first engineering skill pack |
| Working tree | Clean (pending commit) |
| Product code modified | NO |
