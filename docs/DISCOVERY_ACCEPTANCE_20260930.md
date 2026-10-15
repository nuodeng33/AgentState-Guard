# 发现与接入修正验收（2026-09-30）

## 交付范围

已更新源码、追加 migration 17、最小页面和本地预览 `http://127.0.0.1:8787`。本轮实现程序/实例/会话库自动发现和明确展示；没有实现发送任务、接管外部进程或多 Agent 编排。原先的文件保护与恢复继续使用同一后端。

## 真实环境结果

| 对象 | 实际结果 |
| --- | --- |
| Windows Codex | 4 个运行实例、3 处程序位置；不能推断为 4 个账号或会话库 |
| Windows Zcode | 自动找到程序和 OS 用户目录下的库，38 条任务会话全部通过明确 ID 对应；6 个具有已核验 Electron 辅助角色的进程折叠，其余 5 个观测实例保持独立，不按名称强行合并 |
| Docker agent-codex-b | 安装入口可见，无 Codex 运行实例，真实历史会话 152 条可读 |
| Docker agent-k3 | Kimi 运行实例可见，真实会话 75 条可读；安装入口包括 Kimi、CloudCLI、Claude、CCR |
| Kimi 包装进程 | PID 1 显示实际 `/usr/local/bin/node`；没有核实脚本入口，因此不伪造安装程序与 PID 关联 |
| CloudCLI | 安装有证据，真实默认索引不存在，显示 `SESSION_STORE_NOT_PRESENT`；真实成功读取仍未验收 |
| Windows Codex 库 | 两处可读库分别返回 28、125 条，但状态为 `DEGRADED / SESSION_METADATA_PARTIAL`，不声称完整读取 |
| WSL / 文件恢复 | Ubuntu 及 Windows、Docker bind mount、named volume、可写层的可丢弃真实目录记录/撤回回归通过 |

最终本地 HTTP 查询完成，应用/来源/库/会话分页无 HTTP 错误；浏览器已核对实际 Agent 的来源、稳定短 ID、Kimi 的 PID/真实程序路径和工作目录，以及无需手动刷新持续变化的观察时间。报告不含标题、正文或原生会话 ID。证据：[HTTP 结果](../reports/core-protection-20260929/discovery-live-http.json)、[只读环境核对](../reports/core-protection-20260929/discovery-live-inventory.json)。

## 自动更新与正确性

真实临时 Python 进程测试未发送任何刷新 POST，新 PID 自动进入未分类程序实例，退出后自动消失。最终实测出现 14.83 秒、消失 13.69 秒；这包含后台扫描耗时，不是 5 秒内到达保证。后台间隔 5 秒、安装/库盘点 30 秒、可见页面 GET 间隔 5 秒。瞬时进程可能遗漏。

Windows 进程枚举已去掉随后会被丢弃的重复元数据读取，保留采集前后启动身份检查。源码复核另外修正了：相对脚本入口使用进程 cwd；安装事实与运行事实独立计时；失联/读取失败时不沿用旧成功界面；来源 GET 纯读，消除轮询重复写库争用。未确认身份的程序只作为候选。

证据：[自动生命周期](../reports/core-protection-20260929/discovery-automatic-lifecycle.json)。

## 自动化与兼容验证

- 最终全后端：1176 passed，9 skipped。跳过的环境项目按明确 opt-in 单独运行，另一个签名/平台条件测试仍依赖其运行条件。
- 真实 Windows/Docker/WSL 回归：45 passed，0 skipped。
- 最终定向发现、API、进程和会话测试（含真实自动生命周期与 WSL reader）：101 passed。
- 最终前端完整套件：156 passed（26 个测试文件）；TypeScript 和最终生产构建通过。默认候选路径与确已发现的会话库使用不同文案，不把不存在的默认位置标为成功发现。
- 本轮共享发现模块、API、迁移和新测试 Ruff 通过；Git diff 空白检查通过。已有 FastAPI 生命周期弃用提示、3D chunk 大于 500 KiB 提示不在本轮改造范围。

报告：[全后端](../reports/core-protection-20260929/discovery-regression-final.xml)、[真实环境回归](../reports/core-protection-20260929/discovery-live-regression.xml)、[定向验收](../reports/core-protection-20260929/discovery-final-targeted.xml)。

升级前使用 SQLite backup 保存数据库，先在新副本验证 migration 17 的完整性、旧表内容和历史会话 ID，再升级原服务。原服务升级后再次检查：2 个 Work、1 条既有 Agent 人工关联及 50 条旧会话 ID 保留，schema=17，integrity_check=ok。迁移 15/16 未改写；旧会话标为历史来源未核验。旧 Docker 来源别名、停用优先、旧操作回执幂等和重启断开意图有专门回归。

证据：[最终迁移核对及备份路径](../reports/core-protection-20260929/discovery-final-migration.json)。原服务通过有序退出后重新启动，临时 8791 验证服务已结束。

## 剩余边界

控制操作全部不可用；路径展示不产生文件恢复或执行授权。未识别提供方、未登记的其他用户私有配置、无权限项和不支持的库格式不能保证发现。宿主 Codex 两库部分读取、CloudCLI 真实成功查询及更低显示延迟仍有明确限制；不得据此宣称整个 ASG 核心或实时交互完成。

实现契约见 [发现与接入说明](CORE_DISCOVERY_MANAGEMENT.md)。
