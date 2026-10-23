# Windows 安装验收连续记录 — 2026-10-23

基准179876b；黑窗口修复在独立分支fix/windows-native-install，避免取消基准安装包构建。此次无合并或发布。

- 原始默认数据约3.1MiB，逐文件SHA-256双份备份后隔离；测试安装使用新建默认数据目录。当前没有将测试数据恢复到业务目录。
- 40693d7旧MSI静默新装exit0，已启动实际Tauri主程序及打包Core；原生WebView可访问树包含真实页面和已安装路径。窗口抓取timeout、点击geometry unavailable，键盘未观察到焦点变化，因此不声明原生交互完整验收通过。
- 已创建一次性Work及受保护A/B/C文件，作为后续直接升级的持久记录。
- 179876b实际PyInstaller sidecar（ZIP digest已核对）强杀恢复通过：运行中已有1步应用时终止实际打包进程，无fault hook。重启自动COMPENSATED；外部编辑场景NEEDS_ATTENTION并保留字节。两场景重复请求/对账不重放，数据库integrity=ok；测试进程已退出。
- 黑窗口根因：core.runner及allowlisted WSL runner未设置Windows CREATE_NO_WINDOW，版本/环境探测可创建可见控制台。修复只改变子进程创建标志；保留shell=False、allowlist及空环境限制。46项相关回归、本机完整1393通过/13跳过。Ruff与diff检查通过；类型标注及check=False显式化不改变既有返回语义。
- MSI真实升级、新包原生交互、最终恢复与清理结果仍待后续实测补充，不能用新装替代升级。


## 最终执行结果

产品候选4378c6e6f945a58333017833d76dccfb7f2f7730，Core39924182956与Windows39924183141全部成功。

- 40693d7旧包新装exit0；直接升级179876b，再直接升级4378c6e，均exit0。ProductCode依次更替，UpgradeCode不变；未用卸载重装冒充升级，Work/要求/保护记录保留。
- 最终主程序与sidecar逐字节哈希匹配MSI行政提取载荷。独立EXE归档有3字节差异，因此不用独立EXE哈希替代MSI载荷哈希。
- 完整MSI运行时：中英文8页面共16个页面状态通过，环境重复进入/刷新，无页面错误或API失败。测试为实际浏览器连接实际打包Core；原生窗口用户实测“环境不再弹黑窗口”。原生截图/点击工具geometry/capture受限，未声称全部原生鼠标交互验收。
- 实际打包Tauri进程树在恢复执行中终止，重启自动COMPENSATED；外部编辑场景NEEDS_ATTENTION，字节保留；重复请求和对账不重放。两场景无生产fault hook。
- Work/成果/布局/验收与文件恢复历史重启读回通过。完整MSI再次升级后仍能读回上述记录。PowerShell实际桌面健康/归属/清理smoke通过。
- 测试包卸载exit0；8787/18787关闭、测试进程退出。原始6个文件已还原，每个SHA-256与测试前一致；保留独立原数据备份及压缩测试现场。
- 两次临时页面驱动失败源于固定等待与中文字符长度假设；失败日志保留，最终改为等待真实加载完成，不修改产品断言或掩盖HTTP错误。

物理手机、所有原生窗口按钮的自动输入仍未验收；它们与已验证的打包安装/升级、真实恢复和用户确认分别表达。结构化证据见[最终本机报告](verification/WINDOWS_INSTALL_FINAL_20261023.json)。清理空间在后续实测追加。
