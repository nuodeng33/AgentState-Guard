/**
 * Demo-only fixture store. Loaded exclusively when built with VITE_ASG_DEMO=1
 * (see main.tsx); production builds never import this module.
 */

export const MAIN = '7d1c9b52-4a3e-4f0b-9c61-2f8e5a1d3b70';
const W2 = 'a8f2e611-3c4d-4b7a-8e19-6d0c2b9f4e21';
const W3 = 'c3b9d7a4-1e5f-4c28-b6a0-9f7e3d2c1b84';
const W4 = 'e5d1a3c7-9b2f-4e60-8a4d-1c6b7f9e2a05';

const hours = (h: number) => new Date(Date.UTC(2026, 9, 21, 6, 2) - h * 3600_000).toISOString();

type Artifact = { id: string; title: string; version: number; requirement_version: number; created_at: string;
  body: string; source: string; source_run_id: string | null };
type Task = { id: string; title: string; body: string; assignee: string | null };
export type DemoWork = { id: string; title: string; owner: string; revision: number; requirement_version: number;
  updated_at: string; requirements: string[]; tasks: Task[]; artifacts: Artifact[]; view: { revision: number; state: unknown } };

const REQ_V1 = `## 目标

Windows 上由 ASG 托管的 Codex 会话在网络或进程断开后，不能丢失已确认写入，也不能把未完成的执行报告成"已完成"。

## 范围

- 受管会话断线、休眠唤醒、命名管道关闭
- 已落库输出与缓冲区重试`;

const REQ_V2 = `${REQ_V1}
- 重连后不重复发送已送达的输入

## 验收

1. 断线期间产生的输出在重连后完整可见
2. 状态机不出现"已完成"假报`;

const REQ_V3 = `## 目标

Windows 上由 ASG 托管的 Codex 会话在网络或进程断开后，**断线后自动恢复，或明确报告不可恢复**；已确认的写入不得丢失，未完成的执行不得报告为"已完成"。

## 范围

- 受管会话断线、休眠唤醒、命名管道关闭三种场景
- 已落库输出与未落库缓冲区：保存失败时保留缓冲并重试
- 重连后不重复发送已经确认送达的输入
- 错误归因：区分网络中断、进程退出和权限拒绝

## 不在范围内

- WSL 与 Docker 环境下的受管执行（另立工作）
- Kimi 的目录权限边界

## 验收

1. 连续 50 次人为断线，输出在重连后完整可见，没有重复片段
2. 状态机在任何断线时刻都不出现"已完成"假报
3. 不可恢复时，界面给出具体原因和下一步，而不是停在"连接中"
4. 用户确认复核记录后再验收，复核通过不等于验收

## 参考

- \`agentguard/execution/managed_session.py\`
- 2026-10-17 完整回归：1300 通过、26 跳过、0 失败`;

const TASKS: Task[] = [
  { id: 't-reconnect', title: '重连退避与会话恢复策略', assignee: 'CODEX', body: `## 任务说明

按要求 v3 第 2 节实现断线后的重连退避：首次 1 秒，指数增长，上限 30 秒。

- 恢复前先读取已落库的最后序号
- 只补发未确认的输入
- 超过 5 次失败转为"不可恢复"，附带原因码

**完成标准**：断线复现记录中的 3 个场景全部通过。` },
  { id: 't-queue', title: '断线期间的写入队列处理', assignee: null, body: `## 任务说明

断线期间收到的输出先进入本地缓冲区，落库成功后再出队。保存失败时保留缓冲，按退避重试。

尚未分配执行者。` },
  { id: 't-pipe', title: '命名管道断开时的错误归因', assignee: 'CLAUDE_CODE', body: `## 任务说明

命名管道关闭可能来自进程退出、权限变更或系统休眠。需要把这三类原因分开，返回稳定的原因码：

- \`PIPE_CLOSED_PROCESS_EXIT\`
- \`PIPE_CLOSED_PERMISSION\`
- \`PIPE_CLOSED_SUSPEND\`` },
  { id: 't-sleep', title: '复现休眠唤醒后的断线', assignee: 'CODEX', body: `## 任务说明

在 Windows 11 上用 \`powercfg /hibernate\` 复现休眠唤醒后的断线，记录时间线和日志位置。` },
  { id: 't-regress', title: '回归测试：连续 50 次断线', assignee: 'CLAUDE_CODE', body: `## 任务说明

编写回归脚本，连续 50 次人为断线并核对输出完整性、状态机和输入去重。` },
  { id: 't-copy', title: '用户文档：断线提示文案', assignee: 'KIMI', body: `## 任务说明

为"正在重连""已恢复""无法恢复"三种状态写界面文案，中英文各一份，说明下一步该做什么。` },
];

const ARTIFACTS: Artifact[] = [
  { id: 'a-repro', title: '断线复现记录', version: 1, requirement_version: 2, created_at: hours(30), source: 'AGENT_RUN', source_run_id: 'run-sleep-01', body: `## 复现环境

Windows 11 23H2，ASG 0.9.0.dev0，Codex CLI 本机会话。

## 场景

| 场景 | 复现次数 | 结果 |
|---|---|---|
| 休眠唤醒 | 5 | 5 次断线 |
| 管道关闭 | 5 | 5 次断线 |` },
  { id: 'a-repro', title: '断线复现记录', version: 2, requirement_version: 3, created_at: hours(2), source: 'AGENT_RUN', source_run_id: 'run-sleep-02', body: `## 复现环境

Windows 11 23H2，ASG 0.9.0.dev0，Codex CLI 本机会话，受保护目录 \`C:/Work/asg-sandbox\`。

## 场景与结果

| 场景 | 复现次数 | 断线 | 输出丢失 |
|---|---|---|---|
| 休眠唤醒 | 10 | 10 | 0 |
| 命名管道关闭 | 10 | 10 | 2 |
| 网络切换 | 10 | 4 | 0 |

## 发现

1. 管道关闭时，最后一段未落库输出有 2 次丢失，原因是缓冲区在进程退出前被清空。
2. 休眠唤醒后重连成功，但状态短暂显示为"已完成"，属于假报，需要修正。

## 日志位置

- \`%LOCALAPPDATA%/AgentGuard/logs/session-2026-10-21.log\`
- 事件序号 1841–1907` },
  { id: 'a-state', title: '重连状态机说明', version: 3, requirement_version: 3, created_at: hours(5), source: 'USER_EDITED_RUN_OUTPUT', source_run_id: 'run-reconnect-03', body: `## 状态

- **已连接**：可发送输入
- **正在重连**：保留草稿，不发送
- **已恢复**：补发未确认输入后回到已连接
- **不可恢复**：给出原因码和下一步

## 约束

重连成功不代表工作在推进；只有新的执行事件才更新运行状态。` },
  { id: 'a-regress', title: '回归测试报告', version: 1, requirement_version: 3, created_at: hours(20), source: 'AGENT_RUN', source_run_id: 'run-regress-01', body: `## 结果

- 50 次断线中 48 次输出完整
- 2 次在管道关闭场景丢失最后一段输出（与复现记录一致）
- 状态机假报 0 次

**待复核**：需要用户确认 2 次丢失是否在本工作范围内修复。` },
  { id: 'a-codes', title: '错误码对照表', version: 1, requirement_version: 3, created_at: hours(26), source: 'MANUAL', source_run_id: null, body: `| 原因码 | 含义 | 界面提示 |
|---|---|---|
| \`PIPE_CLOSED_PROCESS_EXIT\` | 执行进程已退出 | 执行已结束，请查看最后输出 |
| \`PIPE_CLOSED_PERMISSION\` | 权限被拒绝 | 检查受保护目录权限 |
| \`PIPE_CLOSED_SUSPEND\` | 系统休眠 | 唤醒后自动重连 |` },
];

const simpleWork = (id: string, title: string, owner: string, v: number, h: number, goal: string, tasks: Task[] = [], artifacts: Artifact[] = []): DemoWork => ({
  id, title, owner, revision: v + tasks.length + artifacts.length, requirement_version: v, updated_at: hours(h),
  requirements: Array.from({ length: v }, (_, i) => `## 目标\n\n${goal}${i ? `\n\n（第 ${i + 1} 版补充了验收条件）` : ''}`),
  tasks, artifacts, view: { revision: 0, state: null } });

export function createStore() {
  const works: DemoWork[] = [
    { id: MAIN, title: 'Windows 受管会话断线可靠性', owner: '诺登', revision: 14, requirement_version: 3, updated_at: hours(0),
      requirements: [REQ_V1, REQ_V2, REQ_V3], tasks: TASKS.map(t => ({ ...t })), artifacts: ARTIFACTS.map(a => ({ ...a })), view: { revision: 0, state: null } },
    simpleWork(W2, '桌面安装包签名与卸载清理', '诺登', 2, 26, 'MSI 安装包使用发布证书签名；卸载后不残留 sidecar 进程和计划任务。',
      [{ id: 't-sign', title: '签名流水线接入', body: '在 Desktop Windows 工作流中接入签名步骤。', assignee: null },
        { id: 't-clean', title: '卸载残留检查', body: '卸载后检查进程、计划任务和用户目录。', assignee: null }],
      [{ id: 'a-msi', title: 'MSI 安装与卸载记录', version: 1, requirement_version: 2, created_at: hours(30), source: 'MANUAL', source_run_id: null, body: '安装、启动、卸载流程通过；卸载后无残留进程。' }]),
    simpleWork(W3, 'Android 设备配对二维码', '诺登', 1, 72, '桌面端生成配对二维码，手机扫码后需要在桌面确认指纹才完成绑定。',
      [{ id: 't-qr', title: '二维码过期与刷新', body: '二维码 120 秒过期，过期后提示刷新。', assignee: null }]),
    simpleWork(W4, '要求版本冲突提示文案', '诺登', 1, 170, '两人同时修改要求时，说明哪个版本已保存、草稿保留在哪里、下一步怎么合并。'),
  ];
  const agents = [
    { agent_id: 'proc-codex-4f21a9', detected_identity: 'CODEX', role: 'CODING_AGENT', lifecycle: 'DETECTED', confidence: 0.94,
      execution_domain_id: 'local', workspace: { status: 'BOUND', binding_ref: 'C:/Work/asg-sandbox' }, reason_code: 'PROCESS_MATCHED',
      uncertainty: false, observed_at: hours(0), evidence_refs: ['evt-4102'], application_id: 'app-codex',
      association: 'USER_LINKED' as const, activity: 'UNKNOWN' as const, presence: 'CURRENT' as const, task_ids: ['t-reconnect', 't-sleep'] },
    { agent_id: 'proc-claude-8c03e7', detected_identity: 'CLAUDE_CODE', role: 'CODING_AGENT', lifecycle: 'DETECTED', confidence: 0.9,
      execution_domain_id: 'local', workspace: { status: 'BOUND', binding_ref: 'C:/Work/asg-sandbox' }, reason_code: 'PROCESS_MATCHED',
      uncertainty: false, observed_at: hours(1), evidence_refs: ['evt-4107'], application_id: 'app-claude',
      association: 'USER_LINKED' as const, activity: 'UNKNOWN' as const, presence: 'CURRENT' as const, task_ids: ['t-pipe', 't-regress'] },
    { agent_id: 'proc-kimi-2b9d14', detected_identity: 'KIMI', role: 'UNKNOWN', lifecycle: 'UNKNOWN', confidence: 0.41,
      execution_domain_id: null, workspace: { status: 'UNKNOWN', binding_ref: null }, reason_code: 'IDENTITY_UNCERTAIN',
      uncertainty: true, observed_at: hours(19), evidence_refs: [], application_id: null,
      association: 'USER_LINKED' as const, activity: 'UNKNOWN' as const, presence: 'NOT_CURRENT' as const, task_ids: ['t-copy'] },
  ];
  const capabilities = { create: true, continue: true, send: true, stream: true, cancel: true, fork: false, write: false, responses: true };
  const sessions = [
    { id: 's-codex', work_id: MAIN, provider: 'CODEX', source_id: 'src-codex', application_id: 'app-codex', store_id: 'store-codex',
      scope_id: 'scope-sandbox', scope_root: 'C:/Work/asg-sandbox', task_id: 't-reconnect', status: 'READY', connection_state: 'CONNECTED',
      native_session_id: 'codex-7f3e', read_only: true, revision: 3, capabilities },
    { id: 's-claude', work_id: MAIN, provider: 'CLAUDE_CODE', source_id: 'src-claude', application_id: 'app-claude', store_id: 'store-claude',
      scope_id: 'scope-sandbox', scope_root: 'C:/Work/asg-sandbox', task_id: 't-regress', status: 'READY', connection_state: 'CONNECTED',
      native_session_id: 'claude-91ac', read_only: true, revision: 2, capabilities },
  ];
  const runs = [
    { id: 'run-reconnect-04', session_id: 's-codex', requirement_version: 3, status: 'WAITING_INPUT',
      input_summary: '实现重连退避，并补发未确认的输入', last_seq: 3, coverage: null, change_refs: [], created_at: hours(0.4),
      pending_responses: [{ event_seq: 3, action_ref: 'act-q1', kind: 'QUESTION', payload: { questions: [
        { id: 'unconfirmed', header: '未确认写入', question: '断线时还没确认送达的输入，重连后要自动补发，还是等你确认后再发？', secret: false,
          options: [{ label: '自动补发' }, { label: '等我确认' }] }] } }] },
    { id: 'run-regress-01', session_id: 's-claude', requirement_version: 3, status: 'COMPLETED', input_summary: '编写连续 50 次断线的回归脚本并运行',
      last_seq: 4, coverage: null, change_refs: [{ path: 'tests/test_managed_reconnect.py' }], created_at: hours(21), ended_at: hours(20), pending_responses: [] },
    { id: 'run-pipe-01', session_id: 's-claude', requirement_version: 2, status: 'CANCELLED', input_summary: '区分命名管道关闭原因',
      last_seq: 2, coverage: null, change_refs: [], created_at: hours(28), ended_at: hours(27.5), pending_responses: [] },
  ];
  const events: Record<string, { seq: number; kind: string; payload: Record<string, unknown>; created_at: string }[]> = {
    'run-reconnect-04': [
      { seq: 1, kind: 'TEXT', payload: { text: '已读取要求 v3。先读取已落库的最后事件序号，再决定补发范围。' }, created_at: hours(0.4) },
      { seq: 2, kind: 'ITEM', payload: { item_type: 'fileChange', status: 'completed' }, created_at: hours(0.35) },
      { seq: 3, kind: 'QUESTION', payload: { question: '断线时还没确认送达的输入，重连后要自动补发，还是等你确认后再发？' }, created_at: hours(0.3) },
    ],
    'run-regress-01': [
      { seq: 1, kind: 'TEXT', payload: { text: '回归脚本已写入 tests/test_managed_reconnect.py。' }, created_at: hours(21) },
      { seq: 2, kind: 'ITEM', payload: { item_type: 'commandExecution', status: 'completed' }, created_at: hours(20.5) },
      { seq: 3, kind: 'TEXT', payload: { text: '50 次断线中 48 次输出完整；2 次在管道关闭场景丢失最后一段输出。' }, created_at: hours(20.2) },
      { seq: 4, kind: 'TEXT', payload: { text: '状态机假报 0 次。报告已整理为成果"回归测试报告"。' }, created_at: hours(20) },
    ],
    'run-pipe-01': [
      { seq: 1, kind: 'TEXT', payload: { text: '开始分析命名管道关闭的三类原因。' }, created_at: hours(28) },
      { seq: 2, kind: 'STATUS', payload: { reason_code: 'USER_CANCELLED' }, created_at: hours(27.5) },
    ],
  };
  const submissions = [
    { id: 'sub-regress', requirement_version: 3, run_id: 'run-regress-01', responsibility: 'CLAUDE_CODE',
      refs: { artifacts: [{ artifact_id: 'a-regress', version: 1 }], files: [] }, current_requirement: true,
      reviews: [], acceptances: [], created_at: hours(19.8) },
  ];
  return { works, agents, sessions, runs, events, submissions, requests: new Map<string, unknown>() };
}

export type DemoStore = ReturnType<typeof createStore>;
