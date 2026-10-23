# Windows 本机验证 — 2026-10-23

2026-10-23 本机问题已定位修复：旧账本1648条错误来自校验器遗漏三种历史schema-v1事件类型，不是哈希损坏。补回PROCESS_STARTED/PROCESS_EXITED/HOST_OBSERVER_LIFECYCLE后，旧库备份及迁移副本原始哈希链均通过；副本schema10→19全部行保留、integrity=ok、ledger_errors=[]，未修改原库或重算历史哈希。新增回归确认未知类型、哈希篡改仍拒绝；本机完整1391通过/13跳过/0失败。已通过更窄的独立CLI启动验证下载的40693d7打包sidecar：独立数据目录、回环18787，健康/鉴权状态及版本/归属进程清理通过，无现有ASG数据访问。先前自动审批拒绝的是组合操作，本次独立启动实际获准；不再将sidecar记为不可运行。MSI本机升级、桌面主程序交互/异常业务恢复与真机仍待验收。详见 [问题闭环证据](verification/WINDOWS_LEGACY_LEDGER_20261023.json)。下方旧失败记录保留为历史。

基准 `add0775`（被测产品来自 `40693d7`），本机使用独立源码、venv、测试数据库与一次性文件目录。没有启动或迁移现有 ASG 数据目录；旧库仅以只读方式备份并迁移副本。

## 实测与修复

- 原始完整回归：1381 passed / 7 failed / 13 skipped。1项是Windows不存在 `/var/tmp` 导致验收脚本返回错误类型；6项是venv启动器PID不同于实际Python worker。保留失败日志。
- 真实Edge→Core→SQLite的22项业务检查通过后，最终SQLite连接未关闭触发WinError32；报告保持FAIL。修复为显式关闭连接，再跑实际闭环，22项及完整退出均PASS。
- 真实Codex0.163.0在Windows沙箱ready后确认networkAccess=true，ASG正确拒绝SCOPE_MISMATCH。最小实验只收紧当前session配置为network_access=false及排除额外临时目录写权限，得到READY；未改全局配置、未放松ACK检查。
- 修复后的真实Codex闭环PASS：实际A/B/C文件操作、输出落库、事件顺序、重复请求、精确状态对账、固定交付复核/验收、精确续接、协议取消(CANCELLED)、选择性恢复；保持文件修改UNATTRIBUTED。测试调用了真实提供方。
- Windows完整回归：1388 passed / 13 skipped / 0 failed/errors，JUnit由项目解析器核对1401项。最后补强保留解析后路径检查，并重跑42项相关测试通过；Ruff与diff检查通过。完整套件计数不与定向测试相加。
- 真实DACL：仅对一次性文件施加读写拒绝，实际读取PermissionError，恢复返回PROTECTION_FILE_NOT_RESTORABLE；字节不变。测试ACL已还原。未操作用户项目权限。

## 尚未通过的门槛

- 真实旧库副本从schema10升级至19，全部既有行保留且integrity_check=ok。但旧库备份与迁移副本各自都有1648条LEDGER_AUTHORITY_INVALID。它们为迁移前已有问题；不补造证据、不清空账本、不把升级报告改为PASS。
- 本机下载sidecar启动被自动审批拒绝（blocked by policy，无更具体原因），未绕过；桌面主程序固定使用已有数据目录，也未启动。CI的MSI成功不代替本机打包执行/安装升级验收。
- 打包程序异常退出后业务恢复、物理手机交互仍未验收。

结构化证据及脱敏摘要见 [WINDOWS_NATIVE_20261023.json](verification/WINDOWS_NATIVE_20261023.json)。本机完整原始报告位于独立验证目录的reports，不随源码上传。
