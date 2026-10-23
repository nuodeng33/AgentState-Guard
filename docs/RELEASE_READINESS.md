# Release readiness — 2026-10-23

当前结论：可验证的本地预览，尚未达到 Windows 成品发布标准。

基准：`feat/release-closure` 从双语版本 `03f4bbc` 派生；当前提交用 `git rev-parse HEAD` 获取。文中只将实际执行过的检查标为通过。

| 验收范围 | 证据与结论 | 边界 |
| --- | --- | --- |
| 后端自动化 | 1341 passed、26 skipped，1367 项；完整 pytest / JUnit 已核对 | 跳过项不能算通过 |
| 双语前端 | 基线 `03f4bbc`：213 测试、正式/demo 构建通过 | 本轮没有改前端组件；浏览器夹具不代表真实模型 |
| 真实浏览器 → Core → SQLite | 创建工作/任务/手工成果、保存布局、独立复核与负责人验收通过 | 没有提供方执行；成果来源为 MANUAL |
| 原生文件保护与恢复 | 实际 A/B/C 文件、只读预览、diff、选择恢复、幂等、撤销、过期预览拒绝通过 | 恢复经真实 HTTP API；未新增恢复 UI |
| 重启持久化 | 正常关闭并重启 Core，新浏览器重新读取工作/布局/验收，API 重新读取恢复操作通过 | 不等同同一浏览器断线自动重连或进程强杀恢复 |
| 手机视口 | 390×844 控制可见，无文档横向溢出 | 不等同完整真机触控验收 |
| 验收报告可信度 | 必需检查必须严格为 true；启动写 RUNNING；异常/中断写 FAIL；真实提供方需显式 --run-live | 强杀可能留下 RUNNING，不能算 PASS |
| CI 接线 | 新增 local-workflow，失败阻止 wheel，保存浏览器证据 | 仅本地修改，尚未推送运行；61b1bf1 的历史 CI 不能代替本次 |
| Windows 实际 Codex 运行 | 待验收 | 本轮没有模型调用 |
| Windows 安装/升级/权限/进程恢复 | 待验收 | Linux 证据不覆盖 DACL、MSI 与桌面安装链 |

运行记录：最终结果见 `artifacts/local-workflow/report.json`；浏览器阶段分别为 `browser-create.json`（7 项）、`browser-reopen.json`（5 项），总报告 12 项。检查数属于不同层次，不累加为测试通过数。全量后端日志/JUnit 保存在运行环境 `/tmp/asg-closure-full.log`、`/tmp/asg-closure-full.xml`。

距成品剩余的关键门槛：

1. 在 Windows 上使用真实 Codex 完成连接、执行、取消、精确续接、文件结果及交付验收，并保留同一代码版本的证据。
2. 验证安装、升级、权限拒绝和进程异常退出后的恢复，再运行本次分支的远程 CI。
3. 对照真实用户路径完成桌面与手机交互验收，收敛仍需 API 操作的文件恢复入口。

这些门槛未完成前，不标记“全部修复”或给出无法证明的完成百分比。复现步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)，阶段契约见 [10-local-release-closure.md](phases/10-local-release-closure.md)。
