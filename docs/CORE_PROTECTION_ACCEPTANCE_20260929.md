# Core 目录保护后端验收记录（2026-09-29）

本记录针对当前本地工作树的 Core 目录保护后端。原 `127.0.0.1:8787` 本地服务已备份、重启并升至 schema 16，服务仍在运行。发布包与新前端不在本轮交付范围。真实环境测试使用隔离状态库和临时目录，未将真实用户项目注册为保护范围。实现契约见 [Core 目录保护后端](CORE_PROTECTION_BACKEND.md)。

## 证据

| 证据 | 已验证结果 | 适用范围 |
| --- | --- | --- |
| [优化前完整回归](../reports/core-protection-20260929/regression-final.xml) | 1129 项：1121 通过、8 跳过、0 失败、0 错误；约 108 秒 | schema 15 阶段基线，不代替本轮优化后的最终全量复测 |
| [优化前真实域与 HTTP 验收](../reports/core-protection-20260929/live-final.xml) | 12 项通过、0 跳过、0 失败；约 103 秒 | schema 15 阶段的 Windows、Docker、Ubuntu WSL 临时范围验收 |
| [优化后完整回归](../reports/core-protection-20260929/optimization-regression-final.xml) | 1143 项：1135 通过、8 跳过、0 失败、0 错误；约 119 秒 | 当前本地工作树的 Python 全量回归，含新增优化和故障收敛用例 |
| [兼容性回归](../reports/core-protection-20260929/compatibility.xml) | 39 项通过、0 失败 | R4 控制变更、契约、产品发现、恢复覆盖与 CLI 监督 |
| [迁移校验](../reports/core-protection-20260929/migration-acceptance.json) | 真实既有状态库的备份副本由 schema 14 升至 15；所有旧表行数和内容哈希不变，2 项 Work 保留，新增 14 张保护表 | 迁移本身的离线完整性校验；在线升级另见下一行 |
| [原服务升级](../reports/core-protection-20260929/live-upgrade.json) | 原库备份后由 14 升至 15；两项 Work 及全部 Work 关联表内容哈希保持一致；新接口鉴权与采集线程正常，最终重启后再次验证 | 原 `127.0.0.1:8787` 状态目录；保护范围仍为 0，未自动登记真实项目 |
| [优化定向回归](../reports/core-protection-20260929/optimization-targeted.xml) | 11 项通过、0 失败；含新增 10 项优化测试及旧 Work 迁移版本断言 | 元数据配额、详情到期、摘要与分页、SQL 有界性、UTF-8/回滚/恢复日志计量、非空 v15 历史升 v16 |
| [优化后真实域与 HTTP 验收](../reports/core-protection-20260929/optimization-live-final.xml) | 12 项通过、0 跳过、0 失败；约 90 秒 | 隔离状态库下的 Windows 本机、Docker 三类存储、Ubuntu WSL 与故障用例 |
| [非空状态库迁移校验](../reports/core-protection-20260929/optimization-migration.json) | 真实 schema 15 状态库备份副本升至 16；旧列内容哈希及 v15 checksum 不变，2 项 Work 保留，`integrity_check=ok` | 隔离副本迁移；现场保护范围为 0，非空保护历史另由定向夹具验证 |
| [原服务 schema 16 升级](../reports/core-protection-20260929/optimization-live-upgrade.json) | 停止并备份后升级至 16；2 项 Work 和全部 `work_*` 表行数/内容哈希不变；未授权请求为 401，采集线程运行且无错误 | 原 `127.0.0.1:8787` 服务；保护范围仍为 0，已核对 `shared_storage`、操作摘要、候选分页和 Kimi 50 条会话/50 个已知 cwd |
| [10,000 文件规模验收](../reports/core-protection-20260929/optimization-scale.json) | Windows 临时目录连续 3 版：捕获约 46 秒；两条 SELECT 的摘要查询约 16 毫秒、响应 2,094 字节；逻辑元数据 18,156,440 字节，内容 99 字节 | 单次本机测量，证明摘要响应与元数据计量边界；不作为通用性能承诺 |

本轮新增优化测试文件的 Ruff 检查通过；不据此声称全仓库 Ruff 通过。上述 XML 记录测试名、结果和时长；在线升级由独立报告佐证，不等同于发布包验收。[由 schema 15 迁移的数据库副本](../reports/core-protection-20260929/optimization-migration-copy-20260929T144443Z.db)保留供复核。

## 能力矩阵

| 范围 | 本地结果 | 证据与边界 |
| --- | --- | --- |
| 权限与持久化 | 已验证 | 保护范围绑定来源、物理目录身份、规范路径和连接 epoch；Work 关联是 `USER_LINKED`，普通文件变更仍为 `UNATTRIBUTED`。迁移副本保留旧表内容。 |
| 捕获、历史、配额 | 已验证 | 单元/HTTP 测试覆盖基线、增改删、版本、差异、保留与配额、损坏内容拒绝。扫描采样有时间跨度；离线期间写入不能补录为完整历史。 |
| 历史摘要与逻辑计量 | 定向及规模验收通过 | `summary` 不展开清单/批次/步骤详情；`details_included` 表示本次响应是否展开，`metadata_available` 表示详情仍可读取。过期详情保留摘要与 Work 链接，单文件历史不把未知误判为缺失。`space.logical_retained_bytes` 计入内容、恢复存储、UTF-8 元数据及可归属范围的管理回执；旧版无法可靠归属的回执留在共享物理库。数据库/WAL 占用由 `shared_storage` 单独报告。 |
| Windows 本机 | 真实域通过 | 临时目录经 HTTP worker 完成注册、捕获、`restore_version`、`undo_operation` 和服务重启后读取；文件适配器另验证实际旧内容留存及对账。 |
| Docker | 真实域通过 | 本地 daemon 下的 bind mount、named volume 和 writable layer 均完成相同 HTTP 流程；拒绝远程 daemon 和失效的挂载绑定。bind mount 已验证 `after_intent`、`after_backup`、`after_publish_link`、`before_receipt`、`interloper`、`external_before_backup` 六类故障。新增发布中断场景和 Docker HTTP 流程的[补充验收](../reports/core-protection-20260929/live-final-docker-faults.xml)共 2 项通过。 |
| Ubuntu WSL | 真实域通过 | 显式连接后，临时目录完成 HTTP worker 的捕获、恢复、`undo_operation` 与重启后读取；未停止或重建用户发行版。 |
| 恢复与故障收敛 | 已验证，保留不确定性 | 预览绑定内容/权限与范围，操作有持久步骤和隔离前像；活动中的 `undo_operation` 预览在回收时保留目标字节但不向响应暴露 `content_b64`。失联、外部改写或无法证实写入结果时保持证据并进入 `NEEDS_ATTENTION`，不能伪报成功或盲目重放。配额收紧或外部写者令安全日志超出预留时先保留证据、暂停新增捕获并报告 `over_quota_bytes`；无进展的重复对账不重复追加日志。`undo_batch` 有自动化覆盖，本次跨域 live HTTP 流程主要验证 `restore_version` 与 `undo_operation`。 |
| 旧恢复入口 | 已验证封闭 | CLI restore、旧 HTTP `confirm: true`、transaction undo 和 HostWorkspace 直接 live restore 需返回 `PREVIEW_REQUIRED`；checkpoint 读取、校验及隔离 `test_restore` 仍可用。 |
| Agent 与会话元数据 | 只读验证 | `agent-k3` 的 Kimi 固定只读接口实际返回 `AVAILABLE`、50 条会话、50 个 cwd、`has_more=true`，未输出正文。升级后的产品会话 API 同样返回 50 条且 50 条 cwd 已知，见在线升级报告。本次[只读聚合报告](../reports/core-protection-20260929/kimi-live-metadata.json)保存了数量、cwd 覆盖和分页状态，未保存会话正文、标识或目录值。会话库记录不证明某 PID 正在执行任务。 |
| WSL 会话库 | 条件受限 | 当前 Ubuntu 用户的默认 Codex 与 Kimi 会话库均不存在，真实查询为 `UNKNOWN`；格式解析和自定义 home 路径只在临时 fixture 中得到正向验证，不宣称实际用户会话可读。 |

## 运行边界

- 单文件可恢复内容上限 4 MiB；单次扫描内容 128 MiB；最多 10,000 项、深度 64。超限、敏感内容、不支持的文件或元数据必须降级、排除或停止，不能形成可恢复承诺。
- 权限恢复限于当前用户能验证和设置的元数据：Windows 当前用户 DACL 与支持的属性，POSIX 当前 uid/gid、普通 mode 位；不提权。目标的父目录必须已存在，不重建整棵目录树。
- 目录采样不是瞬时或整树原子快照，也不能保证捕获每次短暂写入。Core 停止或来源离线期间不产生完整写入历史；外部进程仍可能在预检后改动文件或耗尽空间。
- 来源接入不等于 Agent 执行权。没有 Agent 启动、取消、多 Agent 编排、任务归因或远程 Docker daemon 支持。固定 helper 不接受任意 shell 命令。
- 新保护接口尚无前端工作流、桌面窗口/托盘接线验收。原本地 8787 后端已升级；不代表安装包或其他部署已更新。

## 连接状态与剩余验收边界

Docker 保护连接仅在 helper ping 成功且容器运行 epoch 匹配时成立；失败或重启不能沿用连接。默认只读 Agent 发现保持有效，显式断开后进程及会话观察暂停。断开意图在首次启动探测前恢复，daemon 离线也不会丢掉该意图；普通观察开关不改变目录保护。WSL 无法验证根盘身份时拒绝保护授权。Core 重启后跨环境保护需重新明确连接，历史读取不依赖连接；未完成恢复在来源不可达时保留为待处理，不盲目回放。

未在真实用户发行版上做破坏性的注销/重建试验，身份变化拒绝写入由故障夹具覆盖。WSL 实际用户会话库的正向验收仍缺少现成数据。前端接入保护范围、历史、预览、执行与 `NEEDS_ATTENTION` 流程后，需要另做浏览器及桌面端验收。

## 复杂度收敛

删除已关闭的旧 workspace/CLI 直接恢复实现及仅由其使用的辅助函数，移除未使用的 API 刷新回调。旧入口仅保留拒绝直接恢复的兼容响应；旧检查点读取、隔离测试与 V3 导入继续使用。相关[精简回归](../reports/core-protection-20260929/simplification-regression.xml)为 36 项通过、1 项平台跳过。没有新增任务引擎、第二套 Work 对象或 Provider 插件框架。

运行、只读观察和保护连接分别表达事实：Agent 发现并不授予文件恢复权；保护目录也不依赖 Agent 存活。恢复动作仅由保护服务协调，平台适配器负责实际文件操作，避免多个入口各自实施撤回。

Core 配置变更的准备阶段仅核验本机运行环境，复用相同证据生成逻辑，不再扫描无关 Agent 进程。[鉴权、配置变更与发现回归](../reports/core-protection-20260929/api-final.xml)共 65 项通过；没有放宽权限或证据验证。Windows 测试客户端使用保持连接的真实 HTTP 请求，避免未授权 POST 的短连接竞态。
