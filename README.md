# 🛡️ AgentState Guard

Local-first environment state guard for AI development environments.

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
- **Security**: No Docker socket, no credential storage, path traversal protection

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
- ✅ No Docker socket access required
- ✅ No API key or credential storage
- ✅ No files outside project directory modified
- ✅ No remote network access by default
- ✅ No `sudo` or system package installation

## Status
**v0.9.0.dev0 (Backend Preview)** — Not production-ready. Backend core complete; frontend and E2E verification pending.

## License
Apache 2.0
