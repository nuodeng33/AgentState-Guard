# Release readiness — 2026-10-23

当前结论：Windows 新装 CI 链已通过，产品仍为开发预览；尚未达到完整成品发布标准。

## 固定候选与最终证据

- 产品代码：`40693d78d5e5f7bd9ff046bee72da1e5c1ddea3f`，分支 `fix/codex-empty-store`。后续证据文档提交与被测产品代码分开标识。
- [Core run 39913027492](https://github.com/nuodeng33/AgentState-Guard/actions/runs/39913027492)：completed / success。
- [Desktop Windows run 39913027509](https://github.com/nuodeng33/AgentState-Guard/actions/runs/39913027509)：completed / success，全部7个job成功。
- 本次读取实际终态、job日志及下载产物；ZIP按GitHub提供的SHA-256 digest核验，EXE检查MZ/PE、MSI检查复合文件头并记录文件哈希。结构化索引见 [CI核验记录](verification/CI_40693d7_20261023.json)。原始ZIP、日志、XML保存在本接续工作区 `artifacts/ci-handoff-20261023/`，不随源码提交。
- 先前云端未提交的最终进展依据本次实际证据重建；不是把粘贴记录直接当成验证。首轮4011655的Core配置失败和取消的Windows运行均为历史，不代替本候选。

| 验收范围 | 本候选实际证据 | 边界 |
| --- | --- | --- |
| 后端自动化 | 远端Python3.11、3.12、3.13实际JUnit分别由项目解析器核对：各1375 passed / 26 skipped，1401项，0失败/错误 | 不跨运行累加；跳过不算通过 |
| 双语前端 | Core实际日志244项通过；Linux/Windows前端构建成功 | demo构建沿用8d9a0a7记录；已有3D chunk提示保留 |
| 浏览器→Core→SQLite | 下载的真实Linux UI报告22项严格为true，run_id=b39dc9ad-4471-43e4-8987-ea782c7bb004 | 手工成果MANUAL，无提供方执行或模型调用 |
| 文件保护/恢复UI | 真实A/B/C文件选择恢复、只读预览、diff、撤销、过期预览拒绝、未选C保留；历史重读通过 | 全局已有保护目录；无新目录注册/策略配置 |
| 重启与手机视口 | Core正常重启后新浏览器读取工作/布局/验收与恢复历史；390×844及键盘检查通过 | 非物理手机、非断流自动重连或打包进程强杀恢复 |
| 恢复进程专项 | 本次Windows recovery-process实际JUnit：35通过、0失败/跳过，项目解析器已核对；Linux完整测试亦包含相关回归 | 子进程中断证据不等于打包Core/Tauri、断电或真实DACL拒绝 |
| wheel | 构建、checkout外安装、exact product SHA、pip check通过；下载wheel产物digest已核验 | 非Windows安装升级验收 |
| Windows构建及新装 | 独立sidecar、Tauri、bundled sidecar、MSI安装、打包主程序到Core、严格桌面脚本、进程树清理、卸载和残留检查全部成功 | 不是旧版升级、完整WebView交互或所有真实执行环境验收 |
| 产物完整性 | 下载ZIP digest吻合；实际主EXE和sidecar为PE，MSI格式头有效，逐文件SHA-256已保存 | 未在本机安装/启动；格式校验非签名信任验证 |
| 验收脚本可信度 | 必需检查严格true；RUNNING/FAIL/PASS区分；桌面脚本健康/归属/清理失败必须失败 | 强杀留下RUNNING不能算PASS |

## 剩余发布门槛

1. Windows真实Codex连接、执行、取消、精确续接、文件结果及交付验收。历史真实重跑通过空历史选择，随后被Unix provider sandbox proof拒绝，未进入thread/model；保留FAIL/UNAVAILABLE，不绕过安全约束。
2. 旧版本升级、真实DACL权限拒绝、打包程序异常退出后的业务恢复。当前成功的是新装和卸载CI链，不能覆盖这些场景。
3. 原生桌面与物理手机实际交互验收。已有浏览器视口结果不能替代真机。
4. Android/CodeQL本轮未由fix/** push触发；不宣称五工作流或全平台通过。

## 接续与复现

本地接续目录：`/workspace/projects/AgentState-Guard-release-handoff`；原前端工作区与其分支保留。阶段15的同提交远程证据核验已完成；下一步应安排剩余Windows/真机验收，而不是重复修复已关闭的CI故障。本轮只下载只读证据、更新文档，无产品改动、无模型请求、无合并或发布。

复现步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)，阶段契约见 [15-exact-head-ci.md](phases/15-exact-head-ci.md)。测试计数按单次运行报告；既有684条后端弃用警告及26项跳过不视为已解决。文件展示分页仍为客户端每页50条；过期预览用隔离SQLite调整到期时间验证，不声称等待真实五分钟。真实提供方失败报告、此前阶段结果及旧交接记录作为历史保留。
