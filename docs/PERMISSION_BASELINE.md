# Permission Baseline

**Generated:** 2026-07-23T16:08:02Z
**Version:** Pre-v1.0

## User Settings (`~/.claude/settings.json`)

- **Hash:** 392f13f56b4877201ab2bd51896cb7895e7e8028c3236f9bb7513f23768e6a82
- **Permissions:** None (no permissions block)

## Project Local Settings (`.claude/settings.local.json`)

- **Hash:** 9382839ed0215941f7f0a5588a177c0c287130ae216b42c22f30c139ea3eec9f

### Allow (41 rules)

- WebSearch
- Git read-only: status, diff, log, show, rev-parse, branch, ls-files, check-ignore
- Git local: switch -c, add, commit, tag
- Python local: venv, pip install -e, pytest, ruff, mypy, build, agentguard CLI
- Node lockfile: npm ci
- Npm scripts: run lint, typecheck, test, build, e2e
- Npm audit, npx playwright test
- WebFetch to documentation domains (10 domains)

### Deny (85 rules)

- Git destructive: push, pull, fetch, remote, reset, clean, checkout, restore, rebase, merge, stash drop, stash clear, commit --amend, config --global, -c
- Python global: pip install --user, pip install -g, sudo, apt, apt-get, dnf, yum, pacman
- Npm destructive: publish, unpublish, deprecate, owner, access, token, profile, login, logout, config set, install -g, uninstall -g, exec, npx --yes
- Network tools: curl, wget, scp, rsync, ssh, nc, ncat, socat
- Project read: .env, .env.*, .agentguard/**, *credential*, *secret*, *token*
- Project edit: .env, .env.*, .agentguard/**, .git/**, *credential*, *secret*
- System read: ~/.ssh, ~/.aws, ~/.config/gcloud, ~/.kube, etc.
- System edit: ~/.ssh, ~/.aws, etc., /etc, /usr, docker.sock

## Freeze Rule

"Claude Code permissions must not be modified during v1.0 development. If a permission is insufficient, record the gap in docs/ENVIRONMENT_GAPS.md and continue with other tasks."
