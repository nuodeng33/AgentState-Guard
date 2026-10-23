# Project onboarding acceptance — 2026-10-23

ASG-ONBOARD-001, base `0661c24`. This connects existing backend capabilities to ordinary first-use UI. No backend API/schema/permission changes. No merge, release or Windows restart.

| Path | Result | Evidence |
|---|---|---|
| Session creation → program/session-store registration | PASS | Explicit environment scan, real executable and empty store registered through actual Core/browser |
| Session creation → directory registration | PASS | Explicit registration, server receipt then authoritative scopes refresh, selected new scope |
| Protection policy | PASS | Enabled/pause, 45 days/512 MiB settings persisted; default registration remains 30 days/2048 MiB |
| Explicit session consent | PASS | Directory registration never checks consent or starts a run; Create becomes available only after consent |
| Session creation | API receipt PASS; provider execution blocked | Actual POST returned201. Linux provider connection became UNAVAILABLE / EXECUTION_PROVIDER_SCOPE_UNVERIFIED; no model execution was sent |
| Recovery → directory settings | PASS | Same scope/policy selectable; pause saved; stale recovery previews invalidated on settings change |
| Restart persistence | PASS | Core stopped/restarted; UI shows saved policy, no mutation on reopen; disposable original file unchanged |
| Chinese/mobile viewport | PASS | Chinese controls operated; 390×844 viewport has no horizontal page overflow. This is not a physical-phone result |
| Work/delivery/recovery regression | PASS | Existing real Core/browser22 checks: actual work/artifact/review/acceptance, restore/undo/expired-preview refusal/history/restart |
| Native Tauri / physical phone for new UI | Not verified | Native capture/interaction limitations documented in prior installation report; no physical phone attached. Previous native Environment no-console confirmation remains attributed to4378c6e |

Two real UI harness failures are retained separately from final successful evidence: an inaccurate Playwright label selector (replaced with observed combobox role) and strict boolean assertions against SQLite integer booleans plus an English-region locator after language change. Initial test compiler `exact` option and Python import-order Ruff failures also retained. No product assertion or authority checks were relaxed.

Real acceptance exposed a product gap: the UI blocked NEW sessions from EMPTY/DEGRADED stores that Core explicitly accepts. Both disabled-state and submission guard now use the backend-supported statuses and still enforce source equality, active protection, exact continuation binding and explicit consent.

Full frontend270 tests (38 files); full backend1360 passed/46 skipped (1406 cases, deterministic JUnit parser). Windows/PowerShell/live-specific backend cases are skipped on Linux and retain separate native evidence. Typecheck, production/demo builds, new Python Ruff and diff check pass. Independent design and final verification performed per repository workflow.

Commands:

```sh
.venv/bin/python -m pytest --junitxml=/tmp/asg-onboarding-backend.xml
.venv/bin/python scripts/parse-test-results.py /tmp/asg-onboarding-backend.xml
cd web && npm test && npm run typecheck && npm run build && npm run build:demo
.venv/bin/python scripts/verify_project_onboarding.py --program /path/to/existing/codex --report-dir /tmp/onboarding-report
.venv/bin/python scripts/verify_local_workflow.py --report-dir /tmp/local-workflow-report
```

Browser commands use PLAYWRIGHT_MODULE and CHROME_PATH supplied by the existing isolated test runtime. The onboarding harness uses temporary directories, stops only its owned Core/browser and never reads credentials. Consolidated results: [JSON](verification/PROJECT_ONBOARDING_20261023.json). Final tested product commit: `e3944903a53b192980584f2b62d3134e60f0b6f5`. Documentation-only follow-up commits do not change this tested product identity. Rollback: revert ASG-ONBOARD-001 UI/scripts/docs/static assets; no migration rollback required.

## Final remote result

Product `e3944903a53b192980584f2b62d3134e60f0b6f5`: [Core39939893437](https://github.com/nuodeng33/AgentState-Guard/actions/runs/39939893437) SUCCESS, all6 jobs; [Desktop Windows39939893453](https://github.com/nuodeng33/AgentState-Guard/actions/runs/39939893453) SUCCESS, all7 jobs including packaged runtime/MSI lifecycle. Downloaded Core JUnit ZIPs match GitHub SHA-256 digests; deterministic parser gives1380 passed/26 skipped independently for Python3.11,3.12,3.13. These are individual run results, not added together or substituted for the1360/46 local container result.

中文入口：工作 → 更多操作 → 会话管理 → 创建会话。先展开“接入程序与会话库”主动扫描并登记；选择应用和会话库后，在“管理保护目录”登记项目，核对状态和策略，再单独勾选会话管理授权。工作 → 更多操作 → 文件恢复，也可管理目录保护及保留期/配额。

本机保留源码已快进到产品提交；报告/截图/JUnit/关键失败材料保留在 `asg-native-20261023/onboarding-20261023`。本轮临时传输文件258749884字节已移入统一手动清理文件夹并逐文件核对SHA-256；未永久删除，移动释放主机空间为0。未改原业务数据库或凭据，未合并或发布。

安装包已实际下载核验：GitHub产物ZIP SHA-256 `26286cc5ba84ed0e05ef10902fa12fcd7d70ae3aab1f0b66422a08fdc965d4d4`；MSI SHA-256 `d12bf8e856ace1c58fd56c38a0651a6343128201ce83afa4b21656f554298fb6`，24334336字节；EXE逐一验证MZ/PE及SHA-256。Windows Installer COM读取：ProductVersion0.9.0，ProductCode `{74CA0EB7-36C4-4867-82F4-1584DE417D89}`，UpgradeCode `{E3DC1189-5CBA-53BC-BD91-4A515B7531F2}`，与旧候选UpgradeCode一致、ProductCode不同。最新MSI保留于本机 `onboarding-20261023/package`。本轮新包在Windows CI实际安装/卸载成功，未再次安装到用户机器；此前4378c6e本机直接升级/恢复结果保持独立归属，不冒充新包本机验收。
