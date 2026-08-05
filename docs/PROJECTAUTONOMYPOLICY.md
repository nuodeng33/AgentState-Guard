# Project Autonomy Policy

## Purpose

This project uses `permissions.defaultMode: "auto"` for normal work inside the current isolated project environment. It reduces repeated prompts for ordinary development work while retaining Claude Code's safety classification and the explicit deny rules in `.claude/settings.local.json`. It does not enable `bypassPermissions`.

## Allowed Development Work

Within this project, normal source edits, project-local virtual environments, tests, builds, public dependency downloads, GitHub read-only queries, Git remote inspection, fetches, and ordinary pushes of a feature branch can proceed through the normal auto-mode safety checks. A one-time `git -c http.version=HTTP/1.1 push` may be used for a genuine HTTPS transport compatibility issue.

## Hard Boundaries

The project configuration continues to block destructive or out-of-sandbox actions, including:

- force pushes, remote branch/ref deletion, remote URL changes, and direct pushes to protected or archive branches;
- reset, clean, checkout, restore, rebase, merge, amend, destructive stash operations, global Git configuration, and high-risk transient Git configuration injection;
- GitHub PR merges, workflow dispatch/reruns, release creation, and mutating GitHub API requests;
- privileged commands, OS package managers, global package installation, publishing, account/token management, and remote shell or copy tools;
- reads or edits of project secrets and `.env` files, user credential directories, `/etc`, `/usr`, Git internals, and the Docker socket.

Auto mode does not make public network operations risk-free, does not grant access to secrets, and does not permit host-machine operations outside the configured environment.

## Retry Guard Remains Active

The Bash PreToolUse and PostToolUse Retry Guard hooks remain enabled. They continue to use `${CLAUDE_PROJECT_DIR}/scripts/retry_guard.py`, preserve the third-identical-failure block, bind failures to staged/unstaged/bounded-untracked evidence, exclude their local state file, and reset only the matching command fingerprint when new evidence is present.

## Scope And Rollback

This policy is project-local and intended only for this isolated project environment. It must not be treated as a host-wide policy or copied to a non-isolated workspace without a separate safety review.

To return to conservative behavior, set `permissions.defaultMode` back to `"dontAsk"` in `.claude/settings.local.json` while keeping the deny list and Retry Guard hooks intact, then restart Claude Code so the session reloads the configuration.

## Runtime Verification

The accompanying static test checks the JSON policy, deny-rule set, Retry Guard hooks, and `.gitignore` scope. Permission enforcement itself is loaded at session startup, so a fresh Claude Code session is required before relying on the new auto-mode behavior.
