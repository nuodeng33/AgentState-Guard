# Secret Exposure Audit

**Principle: high functional privilege ≠ high credential visibility.**

## Credential Inventory (names only — NO VALUES shown)

| Category | Location | Agent Can Read | Risk |
|----------|----------|---------------|------|
| Anthropic API | `ANTHROPIC_AUTH_TOKEN` env var | No (not exposed to tools) | Low — env is read-only to Claude Code session |
| DeepSeek API | Injected at CCR level | No (CCR handles) | Very Low — outside sandbox |
| GitHub | `gh auth status` = not logged in | N/A | None — no credentials present |
| SSH | `SSH_AUTH_SOCK` = not set | No | None — no agent socket |
| Docker | Socket absent | No | None — cannot access host Docker |
| Git | No credential helpers | N/A | None — no stored credentials |
| npm | No auth tokens | N/A | None — public registry only |
| pip | No index credentials | N/A | None — public PyPI only |
| CloudCLI | Container port 3001 | Network only | Low — API accessible, not key storage |
| Tailscale | Not installed | N/A | None |

## .env File Scan
- `/workspace/projects/她在雨停以前出现/.env.example` — example file only
- No real `.env` files found in workspace

## Verdict
No credentials are directly accessible from within the sandbox. The only credential present (`ANTHROPIC_AUTH_TOKEN`) is a Claude Code session token that is consumed by the SDK layer and not exposed to tool calls.
