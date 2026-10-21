# 前端视觉重构（feat/frontend-redesign）

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
