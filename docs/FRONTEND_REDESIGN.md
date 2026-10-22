# 前端视觉重构（feat/frontend-redesign）

## 中英文体验优化（2026-10-22）

后续工作分支：`feat/frontend-bilingual`，起点 `61b1bf1`。工作总览、Guard 顶栏和单个 Work 顶栏均可即时选择简体中文、English 或跟随系统；复用原有语言偏好，不新建后端设置。

- 中文补齐缩放及调整卡片大小的可访问名称；英文修正 work item、task、artifact 和 execution 的单复数。关联 Agent 表达任务分配，不声称实际执行。
- 时间随界面语言格式化，无效值显示“时间未知 / Time unknown”；原始时间保留在机器可读属性和悬停提示。
- 用户标题、正文、Agent 身份、协议码和 ID 原样保留。英文界面中的中文示例正文也是原始内容，不会因语言切换自动改写。语言切换不发送业务写请求，不重置布局或表单草稿。
- Markdown 正文只有表格分隔行时回退为可读文本，避免整个应用白屏；仍不使用 HTML 注入。
- 前端完整回归：213 项通过、0 失败、0 跳过；正式/demo 构建通过。10 项双语浏览器检查覆盖桌面与 390px 窄屏，既有 8 项反馈浏览器流程也通过，均为夹具、未调用模型。生产静态资源同步构建；CI 尚未为本地工作分支运行。

双语浏览器检查：先运行 `npm run build:demo`，再从仓库根执行：

```bash
PLAYWRIGHT_MODULE=/absolute/path/to/playwright CHROME_PATH=/path/to/chromium \
  node scripts/verify_bilingual_browser.cjs
```

脚本自建临时回环服务并自动关闭，报告和截图保存到忽略的 `artifacts/bilingual/`。它只验证演示夹具，不启动 Core 或真实提供方。退出后检查原生提供方能力仍需独立验收。

## 怎么看

不需要后端，用演示数据运行：

```bash
cd web
npm install
npm run dev:demo      # 打开 http://localhost:5174/
# 或打包静态文件：npm run build:demo → artifacts/demo-dist
```

演示数据只在 `--mode demo`（`.env.demo` 里的 `VITE_ASG_DEMO=1`）时加载；正式构建不包含任何演示代码。

## 改了什么

- 设计 token：`web/src/styles/tokens.css`（石墨底色、金色 = Work、青色 = Agent、状态色、字体、圆角、阴影、动效）。`app.css` 里的旧变量名全部映射到这些 token，管理页面自动换色。
- 顶部一级导航：`components/AppShell.tsx` 的 `TopBar`，替代原来的左侧栏；"我的工作"总览也使用它。
- 二维关系空间 / 工作台 / 专注阅读：`pages/WorkPage.tsx` + `pages/work.css`。
- 视觉组件：`pages/WorkVisuals.tsx`
  - `WorkCore`：薄壳空心核心
  - `AgentFilm`：可调色的肥皂膜泡泡（`AGENT_TINTS` 是可选颜色）
  - `Icon`：内联 SVG 图标
  - `Prose`：安全的 Markdown 子集渲染（只生成 React 元素，不用 innerHTML）
- 演示数据：`web/src/demo/fixtures.ts`（内容）和 `web/src/demo/install.ts`（拦截 `/api/*`）。

## 约束

- 后端和 API 没有改动。
- 测试里用到的可访问名称（如 "Show relationships"、"Continue workbench"、"Save workspace"）保持不变。图标按钮用 `aria-label` 保留原名称。
- Agent 泡泡只显示 Core 提供的事实：观察状态、承接任务数、"活动未知"，不虚构执行进度。

## 尚未完成

- 悬停任务时伸出子泡泡；目前只点亮关联的连线和 Agent。
- 3D 场景、Agent 关联层和各管理页只换了配色，版式没有重做。
- 管理页仍直接显示原始状态码和 ISO 时间。


## 接续验证（2026-10-22）

- 演示入口所需的 `web/.env.demo` 已随源码提交，仅包含 `VITE_ASG_DEMO=1`；其他 `.env.*` 仍保持忽略。
- 本轮前端测试：199 项通过，0 失败、0 跳过；JUnit 由 `scripts/parse-test-results.py` 校验。
- `npm run build` 和 `npm run build:demo` 均通过；正式静态资源已同步更新，演示产物仍留在忽略的 `artifacts/demo-dist`。
- 检查正式 JavaScript 不含演示拦截器标记，演示构建包含该标记。未修改后端或调用真实模型。
- Vite 仍提示 3D 场景 chunk 超过 500 kB；本轮未重新验收完整视觉交互，上述“尚未完成”项继续保留。
