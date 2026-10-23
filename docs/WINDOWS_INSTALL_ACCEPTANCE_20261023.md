# Windows 安装验收连续记录 — 2026-10-23

基准179876b；黑窗口修复在独立分支fix/windows-native-install，避免取消基准安装包构建。此次无合并或发布。

- 原始默认数据约3.1MiB，逐文件SHA-256双份备份后隔离；测试安装使用新建默认数据目录。当前没有将测试数据恢复到业务目录。
- 40693d7旧MSI静默新装exit0，已启动实际Tauri主程序及打包Core；原生WebView可访问树包含真实页面和已安装路径。窗口抓取timeout、点击geometry unavailable，键盘未观察到焦点变化，因此不声明原生交互完整验收通过。
- 已创建一次性Work及受保护A/B/C文件，作为后续直接升级的持久记录。
- 179876b实际PyInstaller sidecar（ZIP digest已核对）强杀恢复通过：运行中已有1步应用时终止实际打包进程，无fault hook。重启自动COMPENSATED；外部编辑场景NEEDS_ATTENTION并保留字节。两场景重复请求/对账不重放，数据库integrity=ok；测试进程已退出。
- 黑窗口根因：core.runner及allowlisted WSL runner未设置Windows CREATE_NO_WINDOW，版本/环境探测可创建可见控制台。修复只改变子进程创建标志；保留shell=False、allowlist及空环境限制。46项相关回归、本机完整1393通过/13跳过。Ruff与diff检查通过；类型标注及check=False显式化不改变既有返回语义。
- MSI真实升级、新包原生交互、最终恢复与清理结果仍待后续实测补充，不能用新装替代升级。
