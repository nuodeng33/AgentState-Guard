# AgentState Guard v1.0 — Master Specification

## Product
Local-first state guard for AI development environments: Claude Code, CCR, DeepSeek, Docker, WSL, CloudCLI, Tailscale.

## Core Principles
1. Default read-only
2. Local-first
3. CLI and GUI share the same core logic
4. All changes go through plan → checkpoint → apply → verify
5. Never claim recoverable when recovery can't be guaranteed
6. No unencrypted secrets in storage
7. No LLM dependency for core functionality
8. Crash/power-loss recoverable
9. Every state and risk must be explainable
10. No modification of files outside the project directory

## Key Features
- SQLite with formal schema migrations
- Content-addressed blob store (dedup, integrity)
- Transaction engine (plan → preflight → apply → verify → commit / rollback)
- CLI: plan, transactions, undo, verify, test-restore, gc, watch, handoff, incident, export, import, host-probe
- Web GUI: Dashboard, Environment Topology, Checkpoint Timeline, Diff Viewer, Restore Wizard, Drift Monitor, Profiles/Policies, Reports
- Host probe scripts (PowerShell + POSIX)
- Incident bundles, AI handoffs
- Retention policies and garbage collection
- Export/import with integrity verification
- Security: no Docker socket, no credential storage, path traversal protection, audit hash chain
- E2E: Playwright-based full scenario testing
- Packaging: PyPI-ready, Dockerfile, CI workflows

## Non-Goals
- Not a disk-imaging tool
- Not a Docker management panel
- Not an arbitrary command executor
- Not a secrets manager
- Not a cloud SaaS
- Not an LLM agent framework
- Not a replacement for restic/Kopia/Timeshift/Git
