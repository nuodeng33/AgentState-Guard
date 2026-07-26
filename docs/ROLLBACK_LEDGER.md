# Rollback Ledger

| Phase | Start Commit | Time | Test Status | Allowed Paths | Rollback Command | Untracked |
|-------|-------------|------|-------------|---------------|------------------|-----------|
| 00 | 9043c07 | 2026-07-23T16:09:15Z | 107 passed | agentguard/, config/, docs/, tests/, .github/ | git reset --hard pre-v1-gui | None |
| 03-P0A | 1ae3ad8086ae9c3e2713190b16d013d622037169 | 2026-07-26 | Final targeted 107/107; full Windows 229/246 with 0 new failures | agentguard/device_link/, agentguard/api/server.py, tests/test_device_link*, docs/ | Revert local P0-A commits `20331bb`, `7c4277d`, `a0a13e9`, and documentation closeout; do not use hard reset on a dirty tree | None |
