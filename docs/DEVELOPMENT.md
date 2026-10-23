# Source and cloud development

## 2026-10-23：真实恢复进程终止验收

```bash
python -m pytest tests/test_protection_process_crash.py -v --basetemp /path/outside/git/crash-tests --junitxml artifacts/recovery-process.xml
```

仅准备隔离的 A/B/C 文件并杀死本次创建的测试子进程；无需模型、浏览器或现有会话。覆盖四个故障点、原运行时启动补偿、外部编辑保留、原请求幂等和数据库完整性。Linux 使用 SIGKILL，Windows 使用 TerminateProcess；本轮只运行 Linux。基线 UI 没有改动，不重复构建或将旧界面结果冒充新 Windows 结果。Windows CI 轻量 job 已配置，仍需实际运行。

## 2026-10-23：文件恢复界面

打开任意 Work → 更多操作 → 文件恢复。该入口浏览所有已保护目录，不自动修改 Work 关联。选择目录、版本和文件后先预览，再明确确认恢复；成功操作可从全局记录选择文件并预览撤销。未选文件保留，切换选择会使旧预览失效。请求结果不明时保留原命令，可刷新记录核对或重试同一提交；关闭面板不取消后台操作。页面重载只读取历史，不自动重放命令。

面板与执行、交付互斥；中英文切换不改路径、正文、差异和已选文件。没有目录时说明当前无可用记录；本轮不提供新目录注册或策略配置。

## 2026-10-23：真实本地闭环验收

先安装项目开发依赖并执行 `npm --prefix web ci`、`npm --prefix web run build`。将 Playwright 1.64.1 安装到项目外的工具目录，并安装其 Chromium：

```bash
npm install --prefix /path/to/browser-tools --no-save playwright@1.64.1
node /path/to/browser-tools/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_MODULE=/path/to/browser-tools/node_modules/playwright python scripts/verify_local_workflow.py --temp-root /path/to/disposable-parent
```

`--temp-root` 必须是已存在、位于 Git 工作区和受保护系统目录之外的可写目录；该目录只作为随机临时子目录的父目录。CI 使用 `$RUNNER_TEMP`。已有 Chromium 可用 `CHROME_PATH` 指定。脚本启动随机回环端口，只创建隔离测试数据，不读取私人会话，不调用模型。该命令同时执行 Work 与文件恢复 UI：真实文件 A/B/C、只读预览、恢复、撤销、过期预览拒绝及正常重启后的历史读取。过期注入只作用于临时数据库的一条预览，不是生产钩子。结果与截图在 `artifacts/local-workflow/`，只有本次 `PASS` 且退出码 0 才通过；RUNNING/FAIL 都不能作为验收。

真实提供方脚本现在默认阻止执行；明确需要实际 Codex 调用时才使用 `python scripts/verify_managed_session_live.py --run-live --temp-root /path/to/disposable-parent --report artifacts/live-acceptance/native-live.json`。这会调用已配置的真实提供方并修改可丢弃的测试文件。当前 Linux 无模型验收不能代替 Windows 提供方验收。


Updated 2026-10-15. Use the branch containing the dated workspace update; an old
default-branch checkout or published package may not contain these modules.

The current source-sync PR is a development snapshot, not a verified release.
See the source-sync verification entry in [CHANGELOG](CHANGELOG.md) for dated
test results and the correction to the earlier workspace-authority test report.

## Checkout and install

```bash
git clone --branch feat/v1-host-native-closure-sol https://github.com/nuodeng33/AgentState-Guard.git
cd AgentState-Guard
python -m venv .venv
# Linux/macOS; on Windows use .venv\Scripts\Activate.ps1
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd web
npm ci
npm test
npm run build
cd ..
python -m pytest tests/ -q
```

Python 3.11+ and Node.js 22 are the existing CI baselines. GitHub Actions runs the
Core tests, frontend checks and wheel build. Desktop/Android checks have their
own platform requirements. Live Docker/WSL and provider checks require explicit
opt-in and access to those actual environments; do not set live-test variables
in an ordinary cloud unit-test job.

Leave pytest's temporary directory at its default system location, or choose a
fresh system-temp `--basetemp` **outside every Git checkout**. Workspace tests
resolve ancestor Git roots: placing their fake projects under this repository
would incorrectly turn distinct directories into the same Git workspace and
could scan the source checkout. The test entry point rejects this configuration;
it does not skip the affected tests or change production authority rules.

## Run locally

```bash
python -m agentguard ui --port 8787
```

Open `http://127.0.0.1:8787/?workspace=1`. For frontend iteration, run `npm run dev`
inside `web/`; its `/api` proxy targets the Core on port 8787. Cloud editing,
unit tests and builds work without the author's machine or its data. A remote
cloud runner cannot inspect or control the Windows/Docker/WSL environments on
your personal computer; actual provider credentials and permissions must be
configured in the intended execution environment. Keep the Core private; this
guide does not enable public hosting or remote execution access.

## Browser feedback checks without model calls

Install Playwright in a separate tooling environment and its Chromium browser.
Set `PLAYWRIGHT_MODULE` to that module's absolute path if it is not resolvable
from the checkout; optionally set `CHROME_PATH` to an existing Chromium/Chrome
executable. No user-specific absolute module paths are embedded in the script.

```bash
cd web
npx vite build --outDir ../artifacts/work-feedback/dist
cd ..
node scripts/verify_work_feedback_browser.cjs
```

The script owns a temporary random-port server, uses explicit API fixtures and
writes ignored screenshots/results into `artifacts/work-feedback/`. It does not
launch a model. `verify_managed_session_live.py` and the managed-session browser
script are separate **real execution** acceptance tools, not ordinary smoke
tests; they require a deliberately prepared environment and may invoke a provider.
The older browser tool also requires a fixture file produced by that preparation.

## Data, upgrades and evidence

- Default runtime state is local; existing preview databases and agent histories
  are intentionally excluded from Git. A fresh clone starts with its own state.
- Back up an existing SQLite database using the SQLite backup API before an
  upgrade, validate a copy first, then start the new Core. Do not copy a live
  database without its transactional backup procedure. The additive upgrade
  helper is `scripts/verify_managed_session_upgrade.py --help`.
- Keep command idempotency, scope validation, fixed input versions and restoration
  preview requirements intact. Read the [architecture](ARCHITECTURE.md),
  [Work API](WORK_API.md), [execution contract](MANAGED_SESSION_BACKEND.md),
  [discovery contract](CORE_DISCOVERY_MANAGEMENT.md), and
  [protection contract](CORE_PROTECTION_BACKEND.md) before extending the relevant seam.
- Dated acceptance documents distinguish historical local checks, fixtures and
  actual provider tests. Referenced ignored reports are local evidence, not files
  available in a clean checkout. Record fresh results for the environment you use.
