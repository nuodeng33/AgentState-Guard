# Implementation Log

2026-10-23 ASG-CLOSE-001空间交互子项：已接实际关联子泡泡、悬停连续/固定/Escape/可见回收；共享稳定颜色默认值；3D视角与任务/暂停保留、标签避让及键盘控制。独立全前端282通过，实际演示浏览器9项及软件WebGL22项通过；新代码尚未远端CI。用户要求的是全部计划缺口，颜色选择持久化/排队等待必要兼容扩展答复，设备与AI继续推进；不得将本子项记为全任务完成。

2026-10-23 ASG-ONBOARD-001最终产品固定为e3944903a53b192980584f2b62d3134e60f0b6f5；Core39939893437（6个job）及Windows39939893453（7个job）全部SUCCESS。远端三份JUnit逐份SHA-256与解析器核验均1380通过/26跳过；本地270前端、1360后端/46跳过及真实接入/重启和原有22项闭环证据独立保留。容器提供方执行拒绝EXECUTION_PROVIDER_SCOPE_UNVERIFIED、新入口原生操作/物理手机限制仍明确保留；不把会话201视为提供方执行。见[接入最终记录](PROJECT_ONBOARDING_ACCEPTANCE_20261023.md)。

2026-10-23 ASG-ONBOARD-001：已接通创建会话内程序/会话库接入及目录登记，文件恢复中可登记和配置目录保护；共用现有Core，无API/schema变更。未知回执保留原UUID跨面板，收到回执后仅重读权威列表；源不匹配/禁用/退役阻止创建。修复EMPTY/DEGRADED会话库前端误禁用。前端270通过，后端1360通过/46跳过，正式/demo构建与真实Core UI登记/配置/重启证据通过；容器提供方连接明确拒绝EXECUTION_PROVIDER_SCOPE_UNVERIFIED，未发送模型运行。原有真实交付/恢复22项通过；此前Windows/原生记录独立保留。详见[接入验收](PROJECT_ONBOARDING_ACCEPTANCE_20261023.md)。远端CI随后记录。

2026-10-23 清理交付完成：用户改为自行删除。本轮14项旧包/依赖/传输包/已归档测试目录已同盘移至统一文件夹ASG-待手动清理-20261023，约1.89GiB；移动未跟随1977个测试符号链接，未永久删除、未宣称释放空间。最新源码、验收报告、原始数据备份与压缩测试现场保留；移动后原6文件及备份再次逐一SHA-256核对一致。最终移除由用户操作，自动删除不再重试。

2026-10-23 安装收尾：4378c6e Core/Windows CI成功；本机旧包→179876b→4378c6e真实MSI升级通过，最终文件匹配MSI载荷。中英文8页面16状态、Work/交付/恢复历史持久化、实际打包进程树强杀补偿/外部编辑保护通过；用户确认原生环境页不再弹黑窗口。测试安装已卸载，原始6文件逐一哈希还原；清理记录另行追加。原生自动输入与物理手机限制保留。详见[安装最终记录](WINDOWS_INSTALL_ACCEPTANCE_20261023.md)。

2026-10-23 本机安装连续验收：原数据已双份核验隔离，旧MSI新装exit0；179876b实际打包Core强杀恢复/外部编辑保护/重复请求不重放通过。修复后台探测Windows控制台闪现，完整1393通过/13跳过，46项专项通过。原生窗口截图/点击受工具geometry/capture限制，不冒充完整UI验收；直接升级、还原清理继续推进。见 [安装验收记录](WINDOWS_INSTALL_ACCEPTANCE_20261023.md)。

2026-10-23 本机问题已定位修复：旧账本1648条错误来自校验器遗漏三种历史schema-v1事件类型，不是哈希损坏。补回PROCESS_STARTED/PROCESS_EXITED/HOST_OBSERVER_LIFECYCLE后，旧库备份及迁移副本原始哈希链均通过；副本schema10→19全部行保留、integrity=ok、ledger_errors=[]，未修改原库或重算历史哈希。新增回归确认未知类型、哈希篡改仍拒绝；本机完整1391通过/13跳过/0失败。已通过更窄的独立CLI启动验证下载的40693d7打包sidecar：独立数据目录、回环18787，健康/鉴权状态及版本/归属进程清理通过，无现有ASG数据访问。先前自动审批拒绝的是组合操作，本次独立启动实际获准；不再将sidecar记为不可运行。MSI本机升级、桌面主程序交互/异常业务恢复与真机仍待验收。详见 [问题闭环证据](verification/WINDOWS_LEGACY_LEDGER_20261023.json)。下方旧失败记录保留为历史。

2026-10-23 本机接续：Windows完整回归1388通过/13跳过；真实Edge/Core22项与真实Codex执行/续接/取消/验收/恢复PASS，真实DACL拒绝及ACL还原PASS。修复当前session继承网络策略、验收SQLite连接释放、Windows venv worker PID与路径校验。旧库副本升级仍FAIL：迁移前后均1648条LEDGER_AUTHORITY_INVALID；原始行保留，未修改原库。打包程序启动遭自动审批拒绝，本机MSI/升级与打包异常业务恢复未验收。详见 [本机验证](WINDOWS_NATIVE_VERIFICATION_20261023.md)。

2026-10-23 接续最终核验：验收产品代码固定为 `40693d78d5e5f7bd9ff046bee72da1e5c1ddea3f`，分支 `fix/codex-empty-store`。实际 Core run `39913027492` 与 Desktop Windows run `39913027509` 均已终态 SUCCESS；Windows 包含 recovery-process、frontend/sidecar/Tauri 构建、独立与打包 sidecar、MSI 新装→打包主程序到 Core→严格桌面检查→清理→卸载及残留检查。实际下载的三份远端 JUnit 经项目解析器分别核对为 1375 passed / 26 skipped（各1401项，不跨运行累加）；真实 Linux UI 报告22项均严格为true。产物ZIP按GitHub digest核验，EXE/安装包格式与逐文件哈希另行核对。详见 [最终CI核验](verification/CI_40693d7_20261023.json)；未改产品代码、未调用模型、未合并或发布。旧版升级、真实DACL拒绝、打包程序异常后的业务恢复、Windows真实Codex闭环、桌面/手机真机仍未验收。以下记录为历史。

2026-10-23 ASG-TEST-004 远程 CI 推进：用户已明确允许推送 `fix/codex-empty-store`，仍不合并或发布。`4011655` 已推送；Core run `39912640138` 在调度前失败（无 jobs），actionlint 复现 job env 不允许 runner 上下文。将 PLAYWRIGHT_MODULE 原值移到真实验收步骤的 env，actionlint 由 exit1 转为 exit0；产品、协议、权限及依赖不变。设计及独立复核通过，完整后端 1375 passed / 26 skipped（JUnit 1401 项，88.98 秒）；新 SHA 远程验收另行记录，不将配置校验当成 CI 通过。回滚仅 revert 此配置修复，基准 `4011655`。

2026-10-23 真实Codex验收推进：`fix/codex-empty-store` 基于`8e91251`。当前登录复用获用户明确授权；真实尝试定位并修复“安全根目录无历史却被判身份改变”，inspect返回EMPTY、库存为空且revision稳定。缺失/替换/不安全路径仍拒绝；无auth读取或目录创建。关联56passed/2skipped，最终完整1375passed/26skipped（JUnit1401项）；真实重跑已过selection，但Unix provider sandbox proof未验证而拒绝连接，尚未进入thread/model，保持安全边界。Windows真实运行/安装升级权限恢复、准确HEAD远程CI、真机交互仍是4组门槛。

2026-10-23 发布推进：本地桌面smoke假绿修复，20项真实PowerShell控制流检查通过；完整后端1369 passed/26 skipped（JUnit1395项核对）。身份仅PID+出生时间确认后清理；未知/重用进程不杀，健康后主进程必须存活。Windows安装后实际调用已接CI但未远程运行。真实Codex已获当前登录复用授权，首次会话创建失败EXECUTION_STORE_IDENTITY_CHANGED，继续定位而非完成。剩余4组门槛详见发布基准。

2026-10-23 恢复进程强杀证据：`fix/recovery-process-crash` 基于 `8d9a0a7`。完整后端 1349 passed、26 skipped（JUnit 1375 项核对）；6 项 Linux 真实子进程终止检查已通过：四中断点补偿、现有 runtime 启动恢复、外部编辑保留 NEEDS_ATTENTION；重复请求/对账不新增操作或步骤事件。产品/前端/schema 未改，前端244与真实UI22项沿用明确标注的8d9a0a7基线。Windows轻量门禁已接入Tauri构建依赖，Linux保留JUnit；未推送或远程执行。不是打包Core/Tauri强杀或断电验收。当前边界见 [发布基准](RELEASE_READINESS.md)。

2026-10-23 文件恢复界面：`feat/work-file-recovery` 基于 `4feb030`。Work → 更多操作 → 文件恢复，展示全部已保护目录，版本/文件分页、纯文本差异、明确确认恢复与预览撤销均接通现有 API；不改后端协议或数据库结构。完整后端 1343 passed、26 skipped（JUnit 1369 项已核对）；244 前端测试、正式/demo 构建通过；真实 Core/Chromium 总报告 22 项通过，无模型调用。未知回执复用原请求并按 request_id 核对，后台暂停轮询，旧成功不会误作新提交结果。新目录注册、策略配置、Windows/真实提供方与远程 CI 仍不在本轮验收范围。详见 [发布基准](RELEASE_READINESS.md) 和 [阶段契约](phases/11-work-file-recovery.md)。

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

## 2026-10-22 — WS-007/008 bilingual workspace follow-up

- Reused the existing language provider for compact overview/Work selectors; localized counts, icon-button accessible names and display timestamps without rewriting original content, protocol facts, views or drafts.
- Added regression coverage for language switching without business writes, plural counts and separator-only Markdown safety. Empty table data now falls back to literal React text.
- Independent frontend verification: 213 passed / 0 failed / 0 skipped. Production/demo builds and bilingual/feedback browser fixtures passed; no model calls. Shipped static assets rebuilt from production mode. Backend unchanged; no remote push/merge or new CI claim.

## 2026-08-07 — R4-P7 recovery trust boundary

- Added schema v7 bindings from R3 drills and trusted-baseline candidates to Supervision REVIEW sessions, and durable one-time recovery authorizations with bound subject, context, policy, fingerprint, expiry, nonce, and consumption fields.
- Replaced legacy recovery approval authority with atomic Supervision `USER_APPROVED` plus durable authorization issuance. R3 drill execution and trusted-baseline confirmation reject replay, expiry, mismatch, and stale authorization state.
- Implemented trusted-baseline lifecycle: `CANDIDATE` does not equal `TRUSTED`; confirmation revalidates R3 evidence and atomically writes an immutable baseline, consumes the candidate/authorization, and appends ledger evidence; retirement removes trusted projection.
- Extended server-derived recovery facts with R0/R1/R2/R3 and trusted-baseline dimensions. Strict R3 evidence requires a verified ledger, complete drill sequence, matching approved Supervision binding, and absence of scope/failure evidence.
- Added Recovery CLI inspection and baseline lifecycle commands without changing `agentguard restore` or `agentguard test-restore` and without user-provided trust flags.
- Fresh local evidence: 753 Python tests passed in 26.58s; scoped Ruff passed; wheel build passed; clean non-editable wheel install plus Recovery CLI help/read-only show smoke passed. Normal push and CI audit are recorded separately at the release boundary.


- Added Transaction Foundation support through explicit `StateDB.transaction()` ownership and `BEGIN IMMEDIATE` semantics.
- Added R4 append-only Evidence Ledger, deterministic local Policy, v4 independent supervision sessions, and local `agentguard supervise` CLI workflow.
- Retry Guard now resolves its project-local entrypoint through `CLAUDE_PROJECT_DIR`, detects staged/unstaged/bounded untracked source evidence, and preserves third-identical-failure blocking.
- Offline closure regression covers ALLOW completion and ledger reconnect verification, REVIEW approval requirements, BLOCK/UNKNOWN activation denial, and synthetic secret non-persistence.
- Fresh local targeted evidence before this documentation update: Retry Guard 9 passed; supervision-related regression 35 passed; offline closure 3 passed. Full-suite/package/frontend/CI evidence remains a separate final gate.


- Created governance documents (MASTER_SPEC, REQUIREMENTS, ACCEPTANCE_MATRIX, NON_GOALS)
- Created phase contracts (00-baseline-and-governance)
- Created ROLLBACK_LEDGER.md
- Git baseline: 9043c07 (feat/v1-gui-release)
- Tag: pre-v1-gui
- Tests: 107 passed

## 2026-07-27 — Device Link adversarial audit and correction

- Scope: ASDL/1 crypto, pairing FSM, registry binding, challenge/token lifecycle, FastAPI wiring, Android client contract.
- First audit patch correctly introduced 32-byte tokens, monotonic token expiry, duplicate/single-device rejection, exact replay TTL, P-256 DER validation, terminal secret clearing, and Bearer headers, but independent review found blocking regressions.
- RED evidence: FastAPI `/device/v1/status` returned 422 because postponed annotation resolution could not resolve a function-local `Request`; expired pairing could be reactivated; `pair_ttl` stayed at 120 seconds; unvisited expired sessions exhausted `max_sessions`; Android HTTPS-only enforcement broke all HTTP development tests.
- Correction: module-scope `Request`; production Bearer 401/200 tests; absorbing terminal/expiry transition and controlled errors; pair TTL/max-attempt propagation; expiry cleanup before capacity checks; `pair_complete` consumes before binding so expiry cannot leave a registry side effect; restored HTTP development transport while retaining Bearer authentication.
- Fresh verification via `/workspace/venv/bin/python`: 281 Python tests passed; Device Link targeted tests passed 117/117. Android `testDebugUnitTest` succeeded and parsed JUnit XML reported 11 passed, 0 failed, 0 skipped. Python `compileall` and `git diff --check` passed.
- Rollback point: revert only the listed Device Link product/test/document paths; preserve unrelated pre-existing worktree changes.
- Remaining: production TLS/SPKI pinning, dual SAS confirmation, complete challenge transcript/mutual authentication, UUID canonicalization, challenge/session bounded cleanup, and full ASDL/1 endpoint/schema migration.
