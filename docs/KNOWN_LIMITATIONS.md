# Known Limitations — AgentState Guard 0.9.0.dev0

- test-restore: STUB only (does not actually restore to temp sandbox)
- host-probe: STUB (alias for host-import, no shell scripts)
- export/import: NOT_IMPLEMENTED (entry points removed)
- watch: NOT_IMPLEMENTED (entry point removed)
- run: NOT_IMPLEMENTED (entry point removed)
- Playwright E2E: BLOCKED_BY_ENVIRONMENT (no browser binaries)
- GitHub Actions: WAITING_FOR_GITHUB_ACTIONS (not yet pushed)
- File diff: Only tracks paths in restore_whitelist
- Container detection: Heuristic only (/proc/1/cgroup check)
- Version: 0.9.0.dev0, not production-ready
