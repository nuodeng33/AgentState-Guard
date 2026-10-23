# Current State

2026-10-23 真实Codex验收推进：`fix/codex-empty-store` 基于`8e91251`。当前登录复用获用户明确授权；真实尝试定位并修复“安全根目录无历史却被判身份改变”，inspect返回EMPTY、库存为空且revision稳定。缺失/替换/不安全路径仍拒绝；无auth读取或目录创建。关联56passed/2skipped，最终完整1375passed/26skipped（JUnit1401项）；真实重跑已过selection，但Unix provider sandbox proof未验证而拒绝连接，尚未进入thread/model，保持安全边界。Windows真实运行/安装升级权限恢复、准确HEAD远程CI、真机交互仍是4组门槛。

2026-10-23 发布推进：本地桌面smoke假绿修复，20项真实PowerShell控制流检查通过；完整后端1369 passed/26 skipped（JUnit1395项核对）。身份仅PID+出生时间确认后清理；未知/重用进程不杀，健康后主进程必须存活。Windows安装后实际调用已接CI但未远程运行。真实Codex已获当前登录复用授权，首次会话创建失败EXECUTION_STORE_IDENTITY_CHANGED，继续定位而非完成。剩余4组门槛详见发布基准。

2026-10-23 恢复进程强杀证据：`fix/recovery-process-crash` 基于 `8d9a0a7`。完整后端 1349 passed、26 skipped（JUnit 1375 项核对）；6 项 Linux 真实子进程终止检查已通过：四中断点补偿、现有 runtime 启动恢复、外部编辑保留 NEEDS_ATTENTION；重复请求/对账不新增操作或步骤事件。产品/前端/schema 未改，前端244与真实UI22项沿用明确标注的8d9a0a7基线。Windows轻量门禁已接入Tauri构建依赖，Linux保留JUnit；未推送或远程执行。不是打包Core/Tauri强杀或断电验收。当前边界见 [发布基准](RELEASE_READINESS.md)。

2026-10-23 文件恢复界面：`feat/work-file-recovery` 基于 `4feb030`。Work → 更多操作 → 文件恢复，展示全部已保护目录，版本/文件分页、纯文本差异、明确确认恢复与预览撤销均接通现有 API；不改后端协议或数据库结构。完整后端 1343 passed、26 skipped（JUnit 1369 项已核对）；244 前端测试、正式/demo 构建通过；真实 Core/Chromium 总报告 22 项通过，无模型调用。未知回执复用原请求并按 request_id 核对，后台暂停轮询，旧成功不会误作新提交结果。新目录注册、策略配置、Windows/真实提供方与远程 CI 仍不在本轮验收范围。详见 [发布基准](RELEASE_READINESS.md) 和 [阶段契约](phases/11-work-file-recovery.md)。

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

> 2026-10-22 双语前端后续：`feat/frontend-bilingual` 从 `61b1bf1` 开始，已优化即时语言选择、数量/可访问名称/时间显示，并修复 Markdown 分隔行白屏。完整前端 213 passed，正式/demo 构建与双语/反馈浏览器夹具通过。最新范围与运行方式见 [FRONTEND_REDESIGN.md](FRONTEND_REDESIGN.md)。本地提交不代表已推送或 CI 验收；以下旧阶段记录继续保留。

> 当前工作空间与受管执行的能力边界见 [MANAGED_SESSION_BACKEND.md](MANAGED_SESSION_BACKEND.md)，最近的定向修复与历史证据见 [受管会话验收记录](MANAGED_SESSION_ACCEPTANCE_20260930.md)。2026-10-15 已修复断线输出保留，并接通工作级待办、执行上下文与要求变更确认；前端完整 196 项、后端相关 99 项测试通过。8787 已加载最终构建，原两项 Work 保留；浏览器交互检查使用夹具 API，跨环境执行和新的真实提供方运行未验收。下文保留早期 Guard 阶段记录，分支、测试数和 CI 状态均不是当前工作树的验收结论。

**Version:** 0.9.0.dev0
**Branch:** feat/device-link
**HEAD:** local R4-P7 recovery trust boundary series (verify with `git rev-parse HEAD`)

## R4-P7 Recovery Trust Boundary

- Schema v7 stores recovery-drill and trusted-baseline candidate bindings to durable Supervision sessions plus expiry/nonce/consume-bound recovery authorizations.
- `VERIFIED_R3` remains distinct from `TRUSTED`; baseline confirmation requires a separate approved Supervision authorization and one SQLite transaction for consume, state transition, immutable baseline write, and Evidence Ledger append.
- Server-computed coverage exposes R0/R1/R2/R3 and baseline lifecycle facts only from StateDB, Snapshot V3, durable recovery records, and a verified ledger. BLOCK/UNKNOWN, scope drift, stale/mismatched evidence, replay, and retirement fail closed.
- Recovery CLI provides inspection and baseline create/approve/confirm/retire/show commands and does not accept caller-supplied recovery truth.
- Fresh local evidence: 753 Python tests passed; changed-file Ruff passed; wheel built and installed into a clean non-editable venv; Recovery CLI help and read-only show smoke passed. Push and CI audit remain release gates until recorded.


- Local commits establish transaction foundation, schema v3 Evidence Ledger, schema v4 supervision sessions, deterministic policy, project-local Retry Guard evidence, local supervision CLI, and an offline closure regression.
- Fresh pre-document targeted evidence: Retry Guard 9 passed; Supervision/migration/policy/evidence/CLI related regression 35 passed; offline closure 3 passed. Final full-suite and CI evidence must be collected separately.
- Safety boundary: BLOCK and UNKNOWN do not activate, REVIEW requires explicit approval, checkpoint requirements remain active, and session plus ledger writes share one `BEGIN IMMEDIATE` transaction.
- See `docs/R4_P4_SUPERVISION.md` for contracts and explicit non-goals.

## Core CI Status

| Check | Result |
|-------|--------|
| Full pytest (local) | **227 passed, 0 failed, 0 skipped** |
| commit | e6267ea |
| GitHub Core CI | PENDING (pushed, awaiting runner) |

## Changes in e6267ea (committed)

All 9 previous Core CI failures resolved:

| # | Test | Type | Fix |
|---|------|------|-----|
| 1 | test_replay_cache_evicts_properly | B | size() counts live entries only |
| 2 | test_terminal_reject_all_events | A | EXPIRED + FAILED paths trigger state transition |
| 3 | test_full_pairing_flow | B | Added gw.get_status() + /device/v1/status route |
| 4 | test_unauthenticated_read_rejected | B | Same as #3 |
| 5 | test_oversized_payload_rejected | C | 1MB Content-Length cap in HTTP handler |
| 6 | test_replay_mutation_killed | A | Added ReplayCache import |
| 7 | test_expiry_mutation_killed | A | setup() → setup_method() |
| 8 | test_transcript_field_mutation_killed | A | Use TRANSCRIPT_SECRET (separated const) |
| 9 | test_terminal_reactivation_mutation_killed | A | Same as #7 |

## Android Runtime Closure (pending commit)

| Change | Status |
|--------|--------|
| DeviceLinkClientTest.kt (JVM, 7 tests) | WRITTEN |
| DeviceLinkClientInstrumentedTest.kt (emulator) | WRITTEN |
| build.gradle.kts: androidTest deps | WRITTEN |
| server.py: nonce_hex → nonce (field name mismatch) | WRITTEN |
| emulator.yml: boot progress + logcat streaming | WRITTEN |
| emulator.yml: DeviceLinkClient instrumented test step | WRITTEN |

## Device Link MVP (14 requirements)

| ID | Description | Status |
|----|-------------|--------|
| DL-MVP-001..016 | 14 read-only MVP reqs | DESCRIBED in MVP_SPEC |

## Source Lines
| Module | Lines |
|--------|-------|
| crypto.py | 230 |
| pairing.py | 195 |
| gateway.py | 224 |
| api/server.py | 285 |
| **Total** | **934** |

## Test Assertions
| File | Count |
|------|-------|
| test_device_link_crypto.py | 19 |
| test_device_link_pairing.py | 17 |
| test_device_link_audit.py | 20+ |
| test_device_link_integration.py | 7 (HTTP) |
| test_device_link_e2e.py | 28+ |
| DeviceLinkClientTest.kt (JVM) | 7 |
| DeviceLinkClientInstrumentedTest.kt | 1 |
| **Total** | **99+** |

## Next Phase: Phase 1 (Tauri scaffold) + Runtime Closure

## Device Link Stable Development Baseline — 2026-07-27

- Independent adversarial review rejected the first audit patch because FastAPI treated `Request` as a query parameter, pairing expiry could be overwritten, `pair_ttl` was not propagated, expired sessions occupied capacity, and the Android HTTPS-only client could not connect to the HTTP development server.
- Closed the immediate regressions: production Bearer routes now return 401/200 rather than 422; pairing terminal states are absorbing and clear secrets; configured pair TTL and expired-session capacity cleanup are enforced; `pair_complete` consumes before binding so expiry leaves no registry side effect; Android development transport is interoperable over HTTP while retaining `Authorization: Bearer`.
- Fresh local evidence: **281 Python tests passed**; all seven Device Link Python files passed (**117 tests**); Android JVM JUnit XML reports **11 passed, 0 failed, 0 skipped**; `compileall` and `git diff --check` passed.
- Stable development baseline: **YES**. Merge-ready: **NO** because TLS 1.3/server certificate handling/SPKI pinning, real dual SAS confirmation, complete mutual challenge transcript, UUID canonicalization, and full ASDL/1 route/schema convergence remain open.
