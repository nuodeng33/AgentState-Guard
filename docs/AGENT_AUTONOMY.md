# Agent Autonomy — Sandbox Engineering Loop

## Effective Mode
`dontAsk` — pre-approved operations auto-execute; unapproved operations auto-deny.

## Allowed Inside Sandbox
- Git: status, diff, log, add, commit, branch, switch, tag
- Python: .venv, pip install -e, pytest, ruff, mypy, build
- npm: ci, run lint/typecheck/test/build/e2e
- File I/O: project directory only
- WebSearch, WebFetch (documentation domains)
- gh CLI (when authenticated)

## Denied Inside Sandbox
- Git: push, pull, fetch (no remote after credential check)
- Python: pip install --user/-g, sudo, apt
- npm: publish, install -g, config set
- Network tools: curl, wget, ssh, nc
- System paths: /etc, /usr, /var/run/docker.sock
- Credential files: .env, .aws, .ssh, .kube

## Security Model
- **Sandbox isolation (container) = primary security boundary**
- Claude Code permissions = convenience guard, not hard boundary
- deny rules prevent obvious mistakes but do not replace container isolation
- After gh auth login: git push/pull enabled

## Build Loop
```
code → git commit → git push (when authenticated) →
GitHub Actions (core + desktop-windows + android) →
wait/check result → fix → repeat
```
