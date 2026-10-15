# AgentState Guard v1.0 — Master Specification

## Product
Local-first state guard for AI development environments: Claude Code, CCR, DeepSeek, Docker, WSL, CloudCLI.

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
- Security: no mounted Docker socket or generic Docker command API; restricted host CLI observation only (see [discovery boundary](../CORE_DISCOVERY_MANAGEMENT.md)); no credential storage, path traversal protection, audit hash chain
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

## Work-space extension (2026-09-27)

The local work-space direction adds work, requirements, tasks, deliveries, review
and acceptance alongside the Guard core. The first implemented slice is the
[persistent Work API](../WORK_API.md), now including manually saved text artifact
versions and spatial view persistence. Execution records, submission sets,
attachments, review and acceptance are not yet implemented.
Verified local Agent discovery now supports explicit Work and task associations.
Both spatial views read these persisted links; process presence and user assignment
do not establish task execution, completion or authorship. Execution remains unknown
until an execution adapter supplies verifiable events.
The agreed UI consists of a 2D relation space, a multi-content workbench and optional
focused reading. 3D is an optional rest/observation view of the same facts.
Entering a Work hides the overview, other Works and the Guard sidebar. Start
with the central Work core; expand related content only on request. Only actual
Agent records may appear as Agent bubbles. Environment and settings remain
available through compact navigation. Each deeper layer has a working return
control; failed layout persistence must not trap the user in a layer.
Work acceptance and environment safety approval remain distinct. This extension
does not make v1 an arbitrary executor or an LLM agent framework.

## Discovery and connection management slice (2026-09-29)

The current priority is real native/container observation and read-only provider
connection management, before execution orchestration or further spatial UI work.
Container names are never Agent identities. Process-name matches, protocol
handshakes, user task assignments and execution facts remain separate evidence.
The [implementation and verification boundary](../CORE_DISCOVERY_MANAGEMENT.md)
documents this first slice and the still unsupported capabilities.
