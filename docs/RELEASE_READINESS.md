# Release readiness — 2026-10-23

当前结论：可验证的本地预览，尚未达到 Windows 成品发布标准。

基准：`fix/recovery-process-crash` 从恢复界面版本 `8d9a0a7` 派生（闭环基础为 `4feb030`）；当前提交用 `git rev-parse HEAD` 获取。文中只将实际执行过的检查标为通过。

| 验收范围 | 证据与结论 | 边界 |
| --- | --- | --- |
| 后端自动化 | 1349 passed、26 skipped，1375 项；完整 pytest / JUnit 已核对 | 跳过项不能算通过 |
| 双语前端 | 界面基线 `8d9a0a7`：244 测试、正式/demo 构建通过，正式静态资源已更新 | 保留原有 3D chunk 大小提示；无模型调用 |
| 真实浏览器 → Core → SQLite | 创建工作/任务/手工成果、保存布局、独立复核与负责人验收通过 | 没有提供方执行；成果来源为 MANUAL |
| 原生文件保护与恢复 | 实际 A/B/C 文件；UI 完成选择恢复、只读预览、真实 diff、撤销及过期预览拒绝，未选 C 保留 | 全局目录入口，不新增目录注册或保护策略配置 |
| 重启持久化 | 正常关闭并重启 Core，新浏览器重新读取工作/布局/验收，恢复面板重新读取全局操作历史通过 | 不等同同一浏览器断线自动重连或进程强杀恢复 |
| 手机视口 | 390×844 控制可见，恢复确认按钮可滚入可达；工作台无文档横向溢出 | 不等同完整真机触控验收 |
| 恢复进程强杀 | Linux 6 项实测：四中断点、现有 runtime 启动自动补偿、外部修改保留；重复请求/对账不重放 | 非打包 Core/Tauri/进程树强杀，不覆盖断电、DACL或 Windows |
| 验收报告可信度 | 必需检查必须严格为 true；启动写 RUNNING；异常/中断写 FAIL；真实提供方需显式 --run-live | 强杀可能留下 RUNNING，不能算 PASS |
| CI 接线 | 本地 UI 闭环 22 项；新增 Linux 分版本 JUnit 和 Windows 6 项进程终止门禁，失败阻止 Tauri 构建 | 仅本地修改，尚未推送运行；61b1bf1 的历史 CI 不能代替本次 |
| Windows 实际 Codex 运行 | 待验收 | 本轮没有模型调用 |
| Windows 安装/升级/权限/进程恢复 | 待验收 | Linux 证据不覆盖 DACL、MSI 与桌面安装链 |

界面基线运行记录（产品代码本轮未改）：`run_id=90e0f474-c651-4a9a-adcd-946bbe84f94b`，最终结果见 `artifacts/local-workflow/report.json`，22 项全部通过。Work 浏览器阶段为 `browser-create.json`（7 项）、`browser-reopen.json`（5 项）；恢复界面阶段为 `browser-recovery-exercise.json`（8 项）、`browser-recovery-reopen.json`（1 项）。检查数属于不同层次，不累加为测试通过数。全量前后端日志及后端 JUnit 由本轮验证记录保存。

距成品剩余的关键门槛：

1. 在 Windows 上使用真实 Codex 完成连接、执行、取消、精确续接、文件结果及交付验收，并保留同一代码版本的证据。
2. 验证安装、升级、权限拒绝和进程异常退出后的恢复，再运行本次分支的远程 CI。
3. 完成 Windows 桌面与手机真机的实际使用验收。本轮已接通已有保护目录的恢复 UI；新目录注册和保护策略仍未纳入此面板。

这些门槛未完成前，不标记“全部修复”或给出无法证明的完成百分比。复现步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)，阶段契约见 [12-recovery-process-crash.md](phases/12-recovery-process-crash.md)。

本轮补充边界：版本使用现有 full API 每页 50 条，文件每页 50 条为客户端展示分页，不声称服务端文件分页。预览过期验收只修改隔离临时 SQLite 中该条预览的到期时间，随后通过真实界面确认：HTTP 409 / PROTECTION_PREVIEW_EXPIRED，原文件不变；未等待真实五分钟，也未增加生产测试入口。

测试补充：后端 684 条警告主要来自现有 FastAPI on_event/lifespan 弃用提示；前端本轮完整测试无警告。测试跳过和弃用提示保留，不计为通过或已修复。

新增未闭合项：`scripts/test-desktop.ps1` 的 health 请求失败目前只给警告，之后仍显示 Done；该本地辅助脚本尚不能作为严格发布验收。现有远程桌面工作流使用其自身的检查，本轮没有据此宣称安装验收通过。

本轮进程恢复证据：`artifacts/recovery-process/full.xml` 与 `full.log`（后端完整）；`targeted.xml` 与 `targeted.log`（6 项专项）。专项为完整套件的子集，不累加计数。
