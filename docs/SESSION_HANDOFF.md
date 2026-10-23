# R4-P7 Recovery Trust Boundary Handoff

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

## 2026-10-22 当前前端接续说明

- 当前工作：`feat/frontend-bilingual`，从前端改版 `61b1bf1` 派生；准确 HEAD 用 `git rev-parse HEAD` 读取。
- 已完成 Work/总览顶栏即时语言选择、数量表达、中文可访问名称和时间回退；用户正文/机器身份/草稿/布局不因翻译而改写。修复只有 Markdown 表格分隔行时的渲染白屏。
- 验证：213 前端测试通过，正式/demo 构建通过，双语 10 项及既有反馈 8 项浏览器夹具检查通过；无模型调用。保留已有 3D chunk 大小提示。
- 本轮不修改后端/权限/数据库，不推送或合并；真实提供方验收未扩展。详见 [FRONTEND_REDESIGN.md](FRONTEND_REDESIGN.md)。下方为历史 P7 交接，不能用于选择当前前端分支。

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
