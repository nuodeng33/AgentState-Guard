# V1 Host-native Backend / Authority Gap Closure 设计

日期：2026-08-17
基线：`48f37448c3a9af546368fef3bc52bdfc39bc7633`
分支：`feat/v1-host-native-closure-sol`

## 1. 目标

在不改变 K3 冻结视觉层、不新增第二套 authority、不把 Docker/WSL 变成产品前提的前提下，闭合三个 V1 缺口：

1. Windows Host-native Agent workspace 自动成为 server-owned protection scope，并能完成 checkpoint、coverage、diff/evidence、Test Restore、workspace-level Restore 和 post-restore verification。
2. Desktop 从 Core loopback projection 取得 Core 生成的真实 SAS。
3. 普通 Windows 用户启用 Device Link 时，由产品自身完成最小、scoped、可审计的 UAC firewall elevation；不要求手工 PowerShell/netsh，也不修改网络 profile。

## 2. Authority 与安全不变量

- Core 仍是唯一产品 authority；所有 mutation target、execution domain、workspace scope、checkpoint、evidence relation 和 restore set 均由服务端决定。
- `POST /api/v1/recovery/checkpoints`、Test Restore、Restore 继续拒绝 caller-supplied path/domain/manifest。
- Device Link 继续只允许远程 `APPROVE_ONCE` 和 `REJECT`；不开放 recovery 或 generic Core proxy。
- Workspace 观察只证明“路径发生了变化”，不证明具体 Agent 写入。没有因果证据时固定投影 `UNATTRIBUTED`。
- 不跟随 symlink、junction 或其他 reparse point；不跨 workspace root；不做 whole-host backup 或 arbitrary path restore。
- Evidence Ledger 不存绝对路径或文件内容；路径以 workspace-relative display path（仅安全类别）和不可逆 target digest 投影。
- UAC 只用于 Device Link firewall helper，不用于 discovery、checkpoint、Test Restore 或 workspace Restore。

## 3. 复用现有能力

实现继续复用：

- `ProductDiscoveryService`、`ProcessCollector`、`PsutilProcessBackend` 和 `SelfRuntimeAdapter` 的 host/runtime/process observation。
- `StateDB`、SQLite migrations 和单一 Evidence Ledger。
- `SnapshotStore`、Snapshot V3、`RecoveryService`、`RecoveryCoverageService` 和现有 recovery event sequence。
- `R4ReadProjectionService` 的 Changes/Evidence/Recovery 安全投影。
- `PairingManager`、`PairingSession` 和现有 P-256/SAS transcript。
- `DeviceLinkController`、`WindowsLanSelector`、`ScopedFirewall` 和 Tauri sidecar lifecycle。

不新增独立 backup database、第二条 Ledger、第二个 Recovery authority 或 frontend state machine。

## 4. Host-native workspace scope

### 4.1 Discovery authority report

`ProcessCollector` 在同一次只读 process observation 中同时产生：

- 现有可序列化、已脱敏的 `WorkspaceCandidate`；
- 仅在 Core 进程内使用、不可序列化的 exact CWD authority record。

`ProductDiscoveryService.discover()` 保持现有公开返回值。新增内部组合入口返回 `DiscoverySnapshot` 加 exact workspace authority records，API startup/refresh 使用该入口。测试注入的旧 discovery service 若没有该能力，明确返回 scope unavailable，不从 DTO 或 caller input 反推真实路径。

### 4.2 Root resolution

对 RUNNING 且签名属于 `CODEX`、`CLAUDE`、`CLOUDCLI` 或 `CCR` 的 host-native process：

1. 读取同一 process instance 的 exact CWD。
2. 要求绝对、存在、可读取的目录。
3. 拒绝 drive/filesystem root、用户 home 根、Windows/Program Files/AppData/temp 等宽泛或系统目录。
4. 拒绝路径链上的 symlink/junction/reparse point。
5. 在有 `.git` marker 时选择最近安全 Git root；否则选择安全 CWD 本身。
6. 按规范化 root 合并同一 workspace 内的多个 Agent。
7. 恰好一个安全 root 时激活；多个不同 root 时 `WORKSPACE_SCOPE_AMBIGUOUS`；无安全 root 时按真实失败返回 unavailable reason。

Docker、WSL 是否安装或可用不参与此判定。Host-native Windows domain 单独成立。

### 4.3 Durable server-owned binding

新增 migration，保存最小 server state：

- opaque scope/workspace ID；
- execution domain ID；
- local root path（仅本机 DB，不进入 API/Ledger payload）；
- root digest；
- status/reason code；
- observed time 和 source evidence binding。

Scope 更新与 `WORKSPACE_PROTECTION_BOUND` Ledger event 在一个 `BEGIN IMMEDIATE` transaction 中完成。Ledger payload 只包含 opaque IDs、root digest 和 source evidence refs，不包含绝对路径或 Agent attribution。

## 5. Deterministic workspace policy 与 coverage

### 5.1 分类

每个观察对象属于且只属于：

- `restorable`：root 内普通文件；大小在上限内；无敏感路径/内容；权限 proof 可由当前用户可靠捕获；无 reparse/special-file 风险。
- `audit_only`：敏感文件、超大文件或内容不应持久化，但可安全记录不可逆摘要和元数据。
- `excluded`：依赖、cache、generated output、ASG state、VCS internals、symlink/junction/reparse point、unsupported object。
- `unreachable`：stat/read/permission metadata/scan 无法可靠取得，或对象在 observation 中发生竞争变化。

默认 deterministic exclusions 包括 `.git`、`.agentguard`、`node_modules`、`.venv`/`venv`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`.gradle`、`target`、`dist`、`build` 等。规则固定、可测试、带 reason code，不由 frontend 或 caller 提供。

### 5.2 Snapshot V3 workspace extension

保留 Snapshot V3 及现有单文件兼容性。Workspace checkpoint 增加严格验证的 extension：

- workspace ID/root digest；
- root path（仅本地 artifact）；
- coverage records；
- category/reason counts；
- permission proof metadata；
- scan limits 和 observation time。

现有 `manifest` 继续保存 `restorable`/`audit_only` file entries；`blobs` 只包含 restorable bytes。Workspace extension 与 manifest 一同进入 canonical digest。没有 extension 的旧 Snapshot V3 digest/validation 行为保持不变。

API 只投影 category counts、reason summaries、safe relative labels 和 digests，不投影 root path、敏感路径或 raw manifest。

## 6. Checkpoint、Diff 与 Evidence

### 6.1 Checkpoint

`POST /api/v1/recovery/checkpoints` 继续只接受 `{}`：

- 若存在唯一 active verified host workspace，创建 workspace checkpoint。
- 若已观察到 workspace 但 scope 不安全/冲突/不可达，fail-closed，不静默退回 config-only。
- 未观察到任何外部 Agent workspace 时，保留现有 product-config checkpoint，并明确 `scope_kind=PRODUCT_CONFIG`。

Response 增加向后兼容字段：`scope_kind`、`workspace_id`、`coverage`。旧字段和 reason code 保持可用。

### 6.2 Observation 和 diff

Startup/refresh 在记录 discovery 后，对最新同 workspace checkpoint 做 bounded rescan。生成：

- `CREATED`
- `MODIFIED`
- `DELETED`
- `UNREACHABLE`

Ledger 使用 `OBSERVED_CHANGE`，包含 target digest、safe relative label、change kind、coverage category、recovery disposition、checkpoint/scope binding，以及固定 `attribution=UNATTRIBUTED`。不写 observed Agent 为 actor/subject。

重复 observation 使用状态 digest 去重；历史 evidence 保持 append-only。`GET /api/v1/changes` 和 evidence detail 仅投影 allowlisted fields。

### 6.3 Created 文件

Checkpoint 后新建、当前属于 protected/restorable scope 的文件不得 hard-delete，也不得标为已在 checkpoint 中 recoverable。

Restore 顺序：

1. 将这类 post-checkpoint residue 原子移动到 ASG-owned quarantine（位于 product state 下、workspace 外）。
2. Quarantine record 绑定 checkpoint、workspace、original target digest、content/permission digest 和时间；API 不暴露绝对原路径。
3. Quarantine 成功后验证 protected workspace 与 checkpoint 的 restorable set 一致。
4. `audit_only`、`excluded`、`unreachable` 对象不因 Restore 被移动或删除。

若任一 created restorable residue 无法安全 quarantine：

- 不 hard-delete；
- 已完成的 baseline restorable 文件恢复可保留；
- 总结果降级为 `RESTORABLE_SET_RESTORED`；
- post-restore reason 为 `POST_CHECKPOINT_RESIDUE_PRESENT`；
- 不得设置完整 Workspace Restore verified。

## 7. Test Restore、Restore 与 verification

### 7.1 Test Restore

`RecoveryService.test_restore()` 继续管理 service-level event sequence；Host workspace adapter 在隔离目录物化全部 restorable entry，并验证：

- manifest/workspace extension digest；
- content SHA-256 和 size；
- relative path containment；
- 当前平台可证明的 permission metadata；
- restorable count 与 coverage binding。

不触碰 live workspace。

### 7.2 Workspace Restore

Restore 只从 server-loaded checkpoint 和 durable scope binding确定 root/authorized set。它不接受 caller path。

- 对 checkpoint 内 modified/deleted restorable 文件执行 staged write/recreate。
- 对 post-checkpoint created restorable 文件执行 quarantine，而非 delete。
- 不改变 audit_only/excluded/unreachable。
- 单对象失败返回稳定 reason code；尽可能 rollback 已 staged 的 live mutations。
- Ledger 记录成功、降级、失败及 quarantine evidence，不把 partial restore 投影成完整 verified。

### 7.3 Permission / DACL

普通 Host-native Recovery 不触发 UAC。

- POSIX 仅证明当前用户可 capture/restore/verify 的 mode 信息。
- Windows 仅证明当前用户可读取并在目标上恢复/验证的 file attributes 与 DACL/owner subset。
- 对现有文件优先使用不扩大权限的替换策略保留 ACL；对重建文件仅在当前用户可应用 captured descriptor 时进入 restorable proof。
- 无法 capture、apply 或 verify 的对象降级为 `audit_only`/`unreachable`，或使 Restore fail-closed，返回如 `WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE`、`WORKSPACE_PERMISSION_RESTORE_DENIED`、`WORKSPACE_PERMISSION_VERIFICATION_FAILED`。
- 绝不为 workspace Restore 调用 firewall UAC helper 或通用 elevated process。

### 7.4 完整 verified 条件

只有同时满足以下条件才返回完整 Workspace Restore verified：

- Snapshot/manifest/coverage digest 全部有效；
- checkpoint 内全部 restorable 文件 hash/size/permission proof 匹配；
- 所有 created restorable residue 已安全 quarantine；
- 没有 scope drift、unhandled restorable residue 或 rollback uncertainty；
- Ledger 写入和链验证成功。

否则使用明确 degraded/failure reason，不能复用 `RECOVERY_RESTORED_AND_VERIFIED` 冒充完整 workspace 恢复。

## 8. Desktop SAS authority closure

`PairingSession.start_sas()` 生成后在 session 内保存 transient SAS；进入任一 terminal state 时与 pairing secret 一起清除。

Core loopback `GET /api/v1/device-link/pairings/{session_id}`：

- `SAS_PENDING` 时返回真实 `sas`；
- SAS 未建立或 session terminal 时不返回 SAS；
- frontend 不计算、不缓存 authority SAS。

8788 Android SAS response 继续来自同一 `PairingSession` 值。测试必须证明 Desktop poll 与 Android response 相同，并证明 terminal cleanup。

## 9. Windows Device Link firewall privilege path

### 9.1 Helper ownership

Tauri 主 executable 增加启动早期的 strict helper mode。正常 GUI lifecycle 不变。Tauri 启动 sidecar 时通过环境变量传入自身绝对路径；sidecar 不接受 frontend 提供 helper path。

`ScopedFirewall` 在 Windows production 使用 `ShellExecuteExW` + `runas` 启动同一已安装 executable，一次 UAC 完成 fixed apply/remove operation并等待结构化 exit status。

### 9.2 Helper 限制

Elevated mode 只接受：

- operation：`apply` 或 `remove`；
- RFC1918 local address；
- prefix length `1..30`。

Port、protocol、direction、rule names、Private profile、local/remote scope 均为编译期固定值。Helper 在 elevation 后重新检查 address 属于 up/physical/default-route/Private adapter；不调用 `Set-NetConnectionProfile`，不改变 Public→Private。

用户取消 UAC、helper 缺失、签名/path 不可信、profile 不是 Private 或 rule mutation 失败时 fail-closed，使用独立 reason code。只有 firewall 成功后才启动 8788 listener。

### 9.3 审计

Helper 不输出 token/path/secret。Core lifecycle 保存 bounded result：operation、fixed rule IDs、candidate address digest、status、reason code、time。UAC 只授权 firewall operation，不提升 sidecar 或 workspace recovery。

## 10. 最小合同变化

- Recovery action/result 增加 `scope_kind`、`workspace_id`、`coverage`、`post_restore_status` 和 quarantine/residue counts。
- Recovery projection 增加 workspace coverage 和完整/degraded restore distinction，移除 workspace checkpoint 上的 `PRODUCT_CONFIG_TARGET_ONLY` limitation。
- Changes/Evidence allowlist 增加 `change_kind`、`coverage_category`、`recovery_disposition`、`attribution` 和 safe relative label。
- Pairing status在 `SAS_PENDING` 增加 server-owned `sas`。
- Device lifecycle error codes区分 elevation unavailable/declined/failed 与 LAN/profile failure。

所有新增字段向后兼容；现有 request DTO 不增加 authority input。K3 只需消费字段，不由本分支修改视觉实现。

## 11. TDD 与验证

先写 RED tests，再逐个 root cause 变 GREEN，WIP=1：

1. Host-native exact authority、safe root、same-root multi-agent、ambiguous/unreachable、Docker/WSL absent。
2. Deterministic coverage 四分类、sensitive/generated/reparse/permission/scan race。
3. Workspace checkpoint artifact/digest 和旧 Snapshot V3 compatibility。
4. Create/modify/delete observation、Ledger 去重、`UNATTRIBUTED` projection。
5. Test Restore 与 multi-file restore。
6. Created-restorable quarantine；quarantine failure 必须降级且不删除 residue。
7. Windows current-user permission proof；capability absence不触发 UAC。
8. Desktop/Android同一 SAS 和 terminal cleanup。
9. Firewall helper strict args、Private-profile recheck、UAC decline/failure、fixed netsh scope。
10. Product API roundtrip、target injection rejection、symlink/reparse/path traversal regressions。

验证顺序：targeted pytest → full pytest → Ruff/compile → Rust unit/check/build（若 toolchain 可用）→ Windows packaged CI。Docker 只可作为 test harness，不能作为 Host-native capability proof。没有真实 Windows standard-user/real device evidence时对应结果保持未验证。

## 12. Affected files 预期

主要范围：

- `agentguard/discovery/agents/*`
- `agentguard/discovery/product.py`
- `agentguard/recovery/*`
- `agentguard/evidence/*`
- `agentguard/storage/migrations.py`
- `agentguard/api/product_recovery.py`
- `agentguard/api/r4_projection.py`
- `agentguard/api/server.py`
- `agentguard/device_link/pairing.py`
- `agentguard/device_link/gateway.py`
- `agentguard/device_link/network.py`
- `desktop/src-tauri/src/*`
- 对应 `tests/*`、合同/状态文档

不修改 React/Compose/K3 presentation。

## 13. Rollback 与 exit criteria

实现拆成少量 scoped commits：workspace authority/coverage，workspace recovery/evidence，SAS/firewall，docs/verification。每个 commit 可独立 revert；不 merge/rebase/reset 基线分支。

Exit criteria：

- Host-native path 不依赖 Docker/WSL capability。
- 唯一 verified workspace 自动成为 server-owned scope。
- 四类 coverage 和 reason code 可验证。
- create/modify/delete evidence 不误 attribution。
- Test Restore 与 authorized restorable-set Restore 有真实验证。
- Created restorable residue 已 quarantine，或结果明确降级且从不 hard-delete。
- 普通 restore 不需要 UAC；permission proof不足时真实降级/fail-closed。
- Desktop 与 Android显示同一 Core-owned SAS。
- Device Link firewall 使用最小 UAC helper且不切换 network profile。
- Targeted/full/CI证据按真实结果报告；未验证项不写 PASS。
