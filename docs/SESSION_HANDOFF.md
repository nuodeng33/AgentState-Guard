# R4-P7 Recovery Trust Boundary Handoff

## Current Status

- Branch: `feat/r4-p7-test-restore`
- Start commit: `60204c233136c5bcf58dfb45c5a477b978a987f8`
- P7 changes are staged for the atomic commit; the ignored `.claude/retry-guard-state.json` is runtime state.
- Frozen legacy workspace `/workspace/projects/agentstate-guard` was not accessed.

## Evidence

- `.venv/bin/python -m pytest -q`: 753 passed in 26.58s.
- Scoped Ruff: all checks passed.
- `.venv/bin/python -m build --wheel`: wheel built successfully.
- Clean venv non-editable wheel install and `agentguard --help`, `agentguard recovery --help`, drill/baseline help, and read-only drill show smoke passed.

## P7 Contract

- Durable Supervision-bound recovery authorization is expiry/nonce/subject/context/policy/fingerprint bound and consumed once.
- R3 evidence requires the complete drill event sequence, valid ledger, approved REVIEW session binding, matching session identity/policy, and no adverse scope/failure events.
- Trusted baseline requires explicit separate approval; candidate, trusted, retired, and immutable states are distinct.
- Recovery projection is server-computed and fail-closed; no CLI caller can assert recovery facts.

## Remaining Release Gates

- Run final staged diff/status check.
- Commit the staged P7 changes atomically with requirement IDs.
- Normal push to `feat/r4-p7-test-restore`.
- Read-only GitHub Actions audit for the pushed SHA.

## Boundaries

No P8, UI, production restore, Windows/WSL recovery, Docker socket, secrets, worktree, child agents, force push, merge, rebase, reset, clean, stash, checkout/restore, or frozen-workspace access.
