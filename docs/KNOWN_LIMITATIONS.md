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
- R4-P4 supervision is local and offline only. P5 AI Supervisor is not implemented; `AI_ASSESSED` is only a reserved ledger event type.
- Claude Code business supervision hooks are not integrated. The Retry Guard hook only prevents repeated failed local tool commands without new evidence.
- P6/P7 recovery realism is not complete. UI and public HTTP API supervision surfaces are not implemented.
- The current system is not a complete MVP and does not claim comprehensive protection or unconditional recovery.
