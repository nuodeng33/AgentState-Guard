# 🛡️ AgentState Guard

Local-first environment state guard for AI development environments.

## Development update · 2026-10-15

**v0.9.0.dev0 remains a development preview.** ASG now adds a Work-centered
Agent workspace to the existing local guard: requirements, tasks, versioned
results and evidence stay with the work; Agents are replaceable execution tools.

- **Workspace:** 2D relationships → aggregate workbench → focused reading, with
  saved layouts and reading positions. The optional 3D view presents the same facts.
- **Discovery:** programs, running instances and session stores have distinct
  identities; automatic updates cover supported local, Docker and WSL sources.
  Discovery and readable history do not grant execution control.
- **Protection:** registered directories, saved file versions, readable diffs,
  selective restore, conflict checks and restart reconciliation share one Core.
- **Managed execution:** Windows Codex has a verified create/continue, output,
  cancel and delivery/review/acceptance flow. Work-level attention, explicit
  context selection and requirement-change confirmation are now connected.
- **Latest fix:** reading task/requirement/result cards no longer competes for
  SQLite's write lock; content errors distinguish busy, missing and disconnected states.

This is a **source update, not a new package or installer release**. For current
code, build from this checkout; an older PyPI package is not evidence of these features.
See the [dated changelog](docs/CHANGELOG.md), [cloud/source development guide](docs/DEVELOPMENT.md),
[execution capability boundaries](docs/MANAGED_SESSION_BACKEND.md) and
[dated verification notes](docs/MANAGED_SESSION_ACCEPTANCE_20260930.md).

```bash
pip install agentstate-guard
agentguard doctor
agentguard ui
```

## Features
- **Diagnostics**: PASS/WARN/FAIL/SKIP/UNREACHABLE health checks
- **Checkpoints**: Snapshot file state with content-addressed blob storage
- **Transactions**: Plan → Apply → Verify → Commit/Rollback engine
- **CLI**: 22 commands for full lifecycle management
- **Web GUI**: Dashboard, topology, timeline, diff viewer, restore wizard
- **Host Integration**: Import host state from PowerShell/POSIX probes
- **Security**: Local session authentication, explicit operation scope, path traversal protection

## Quick Start
```bash
# Install
pip install agentstate-guard

# Health check
agentguard doctor

# Create a baseline
agentguard checkpoint "initial state"

# Web UI
agentguard ui
```

## Security Boundaries
- Docker is optional. Enabled local Docker discovery/protection uses a bounded host Docker CLI channel; the Core does not mount a Docker socket into itself.
- AI advisory is opt-in; provider settings may store a user-supplied API key locally. Credentials and runtime databases are not part of this repository.
- File writes and restores require validated scope and the relevant authorization; observed paths alone grant no write permission.
- ✅ No remote network access by default
- ✅ No `sudo` or system package installation

## Status
**v0.9.0.dev0 — Development Preview.** Not production-ready.

## Known Limitations
- **Restore coverage**: Isolated test restore and selective directory restore are implemented; only validated, retained file versions within supported coverage are recoverable.
- **export/import**: Not implemented (removed from CLI; deferred to v1.1).
- **Verification**: Core, frontend, desktop and Android workflows exist. Dated local test results are not a claim that every platform or current GitHub Actions run passes.
- **Web UI**: Serves on **127.0.0.1 only** by default. Remote access requires explicit `--allow-remote` flag.
- **Not recommended for public network exposure.** V1 transport is loopback-only; `--allow-remote` is an explicit diagnostic override outside the product network model.
- **File monitoring**: Requires explicitly registered protection scopes. Sampling can miss short-lived changes; excluded/sensitive content and expired history may be non-restorable.
- **Managed execution**: Docker Codex sandbox restrictions, unverified Kimi directory enforcement and unverified WSL execution remain explicit limitations. They do not disable separately supported discovery or file protection.

## License
Apache 2.0
