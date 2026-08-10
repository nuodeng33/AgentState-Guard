/**
 * Message dictionaries for the desktop UI.
 *
 * One language is displayed at a time (never bilingual labels). Machine
 * semantics — UNKNOWN, REVIEW, BLOCK, UNREACHABLE, ALLOW, SESSION_UNAVAILABLE,
 * reason codes, session/checkpoint/evidence IDs, SHAs, protocol values — are
 * stable backend tokens and deliberately have NO entries here: they render
 * verbatim in every locale. Field-name labels such as "reason_code" are part
 * of the contract surface and likewise stay verbatim.
 */

import type { Locale } from './locale';

export type MessageKey =
  | 'app.brand.name'
  | 'app.brand.sub'
  | 'app.nav.primary'
  | 'nav.home'
  | 'nav.runtime'
  | 'nav.agents'
  | 'nav.supervision'
  | 'nav.changes'
  | 'nav.recovery'
  | 'nav.devices'
  | 'nav.aiMonitor'
  | 'nav.settings'
  | 'view.loading'
  | 'view.unavailable'
  | 'view.apiFailed'
  | 'view.retry'
  | 'view.sessionUnavailableBody'
  | 'banner.sessionUnavailable'
  | 'banner.readinessDegraded'
  | 'banner.readinessFailed'
  | 'banner.degradedSuffix'
  | 'evidence.none'
  | 'evidence.toggle'
  | 'settings.language'
  | 'settings.languageNote'
  | 'settings.preferenceNote'
  | 'language.system'
  | 'language.en-US'
  | 'language.zh-CN'
  | 'common.yes'
  | 'common.no'
  | 'common.items'
  | 'state.degraded.title'
  | 'state.degraded.body'
  | 'state.unknown.title'
  | 'state.unknown.body'
  | 'home.subtitle'
  | 'home.section.overview'
  | 'changes.unavailable.title'
  | 'changes.unavailable.detail'
  | 'aiMonitor.unavailable.title'
  | 'aiMonitor.unavailable.detail'
  | 'runtime.empty.title'
  | 'runtime.empty.detail'
  | 'runtime.unknownType'
  | 'runtime.uncertain'
  | 'runtime.noCapabilities'
  | 'agents.empty.title'
  | 'agents.empty.detail'
  | 'supervision.empty.title'
  | 'supervision.empty.detail'
  | 'supervision.noDecision'
  | 'supervision.aiAssessmentNone'
  | 'supervision.aiDecision'
  | 'supervision.aiSeverity'
  | 'supervision.recoveryFacts'
  | 'supervision.note'
  | 'recovery.empty.title'
  | 'recovery.empty.detail'
  | 'recovery.degraded.title'
  | 'recovery.degraded.body'
  | 'recovery.unknown.title'
  | 'recovery.unknown.body'
  | 'recovery.r0.title'
  | 'recovery.r0.body'
  | 'recovery.section.level'
  | 'recovery.section.baseline'
  | 'recovery.section.checkpoints'
  | 'recovery.chain.ariaLabel'
  | 'recovery.chain.current'
  | 'recovery.chain.verified'
  | 'recovery.chain.notVerified'
  | 'recovery.chain.currentSuffix'
  | 'recovery.baseline.note'
  | 'recovery.card.title'
  | 'recovery.card.failClosed'
  | 'kv.executionDomain'
  | 'kv.capabilities'
  | 'kv.role'
  | 'kv.confidence'
  | 'kv.workspaceStatus'
  | 'kv.workspaceBinding'
  | 'kv.requiresManualApproval'
  | 'kv.manualApprovalGranted'
  | 'kv.requiresCheckpoint'
  | 'kv.aiAssessment'
  | 'kv.recoveryLevel'
  | 'kv.r1Verified'
  | 'kv.r2Verified'
  | 'kv.r3Verified'
  | 'kv.testRestore'
  | 'kv.trustedBaseline'
  | 'kv.baselineId'
  | 'kv.requestedTargets'
  | 'kv.authorizedSnapshotTargets'
  | 'kv.intactManifestBlobs'
  | 'kv.testRestoreStatus'
  | 'kv.testRestoreVerifiedTargets'
  | 'kv.trustedBaselineId'
  | 'action.approveOnce'
  | 'action.approving'
  | 'action.reject'
  | 'action.rejecting'
  | 'action.sendingApproval'
  | 'action.sendingRejection'
  | 'action.note'
  | 'feedback.sessionUnavailable'
  | 'feedback.notFound'
  | 'feedback.conflict'
  | 'feedback.invalid'
  | 'feedback.unavailable'
  | 'feedback.unconfirmed'
  | 'feedback.unknown';

export type Messages = Record<MessageKey, string>;

const enUS: Messages = {
  'app.brand.name': 'AgentState Guard',
  'app.brand.sub': 'R4 Operations Console',
  'app.nav.primary': 'Primary',
  'nav.home': 'Home',
  'nav.runtime': 'Runtime',
  'nav.agents': 'Agents',
  'nav.supervision': 'Supervision',
  'nav.changes': 'Changes',
  'nav.recovery': 'Recovery',
  'nav.devices': 'Devices',
  'nav.aiMonitor': 'AI Monitor',
  'nav.settings': 'Settings',
  'view.loading': 'Loading {label}…',
  'view.unavailable': '{label} unavailable',
  'view.apiFailed': 'API request failed',
  'view.retry': 'Retry',
  'view.sessionUnavailableBody':
    'A session with the local backend could not be established. No authoritative data is shown.',
  'banner.sessionUnavailable': 'cannot establish a session with the local backend.',
  'banner.readinessDegraded': 'Backend reports degraded readiness (HTTP 503)',
  'banner.readinessFailed': 'Readiness check failed',
  'banner.degradedSuffix': 'Authoritative views may be unavailable or degraded.',
  'evidence.none': 'No evidence refs',
  'evidence.toggle': 'Evidence ({count})',
  'settings.language': 'Language',
  'settings.languageNote': 'Changes take effect immediately.',
  'settings.preferenceNote':
    'Language is a display preference only; it never changes authority, security, or evidence state.',
  'language.system': 'System Default',
  'language.en-US': 'English',
  'language.zh-CN': '简体中文',
  'common.yes': 'Yes',
  'common.no': 'No',
  'common.items': '{count} items',
  'state.degraded.title': '{label} view degraded',
  'state.degraded.body':
    'The backend could not project authoritative {label} state ({reasonCode}). Nothing below is projected authority.',
  'state.unknown.title': '{label} state unknown',
  'state.unknown.body':
    'The backend reports this view as UNKNOWN ({reasonCode}). Do not treat it as healthy.',
  'home.subtitle': 'Authoritative state across the four read views.',
  'home.section.overview': 'Overview',
  'changes.unavailable.title': 'No authoritative changes feed',
  'changes.unavailable.detail':
    'This build exposes no authoritative changes projection. Nothing is shown rather than guessed.',
  'aiMonitor.unavailable.title': 'AI Monitor is not configured',
  'aiMonitor.unavailable.detail':
    'No AI provider is configured on this desktop. Nothing is analyzed, and no authority is inferred.',
  'runtime.empty.title': 'No runtime records',
  'runtime.empty.detail': 'The backend holds no verified runtime facts for this view.',
  'runtime.unknownType': 'Unknown runtime',
  'runtime.uncertain': 'uncertain',
  'runtime.noCapabilities': 'None reported',
  'agents.empty.title': 'No agents detected',
  'agents.empty.detail': 'The backend holds no verified agent detection records.',
  'supervision.empty.title': 'No supervision sessions',
  'supervision.empty.detail': 'The backend holds no supervision session records.',
  'supervision.noDecision': 'NO DECISION',
  'supervision.aiAssessmentNone': 'No AI assessment',
  'supervision.aiDecision': 'decision',
  'supervision.aiSeverity': 'severity',
  'supervision.recoveryFacts': 'Authoritative recovery facts',
  'supervision.note':
    'Actions are limited to one-time Approve Once / Reject on sessions awaiting manual approval, bound to the latest authoritative read. Everything else stays read-only: no activation, no checkpoint creation, no policy change.',
  'recovery.empty.title': 'No recovery checkpoints',
  'recovery.empty.detail': 'The backend holds no checkpoint records. Recovery level is R0.',
  'recovery.degraded.title': 'Recovery view degraded — fail closed',
  'recovery.degraded.body':
    'The backend could not project authoritative recovery state ({reasonCode}). Recoverability cannot be confirmed.',
  'recovery.unknown.title': 'Recovery state unknown — fail closed',
  'recovery.unknown.body':
    'The backend reports this view as UNKNOWN ({reasonCode}). Recoverability cannot be confirmed.',
  'recovery.r0.title': 'No verified recovery (R0) — fail closed',
  'recovery.r0.body':
    'No checkpoint currently meets a verified recovery level. Treat restore capability as unavailable.',
  'recovery.section.level': 'Recovery level',
  'recovery.section.baseline': 'Trusted Baseline',
  'recovery.section.checkpoints': 'Checkpoints',
  'recovery.chain.ariaLabel': 'Recovery level chain R0 to R3',
  'recovery.chain.current': 'current level',
  'recovery.chain.verified': 'verified',
  'recovery.chain.notVerified': 'not verified',
  'recovery.chain.currentSuffix': ' · current',
  'recovery.baseline.note':
    'Trusted Baseline is an independent trust state. Recovery level R3 does not imply TRUSTED.',
  'recovery.card.title': 'checkpoint {id}',
  'recovery.card.failClosed':
    'Fail closed: this checkpoint has no verified recovery level ({reasonCode}). Do not treat it as restorable.',
  'kv.executionDomain': 'Execution domain',
  'kv.capabilities': 'Capabilities',
  'kv.role': 'Role',
  'kv.confidence': 'Confidence',
  'kv.workspaceStatus': 'Workspace status',
  'kv.workspaceBinding': 'Workspace binding',
  'kv.requiresManualApproval': 'Requires manual approval',
  'kv.manualApprovalGranted': 'Manual approval granted',
  'kv.requiresCheckpoint': 'Requires checkpoint',
  'kv.aiAssessment': 'AI assessment',
  'kv.recoveryLevel': 'Recovery level',
  'kv.r1Verified': 'R1 verified',
  'kv.r2Verified': 'R2 verified',
  'kv.r3Verified': 'R3 verified',
  'kv.testRestore': 'Test restore',
  'kv.trustedBaseline': 'Trusted baseline',
  'kv.baselineId': 'Baseline ID',
  'kv.requestedTargets': 'Requested targets',
  'kv.authorizedSnapshotTargets': 'Authorized snapshot targets',
  'kv.intactManifestBlobs': 'Intact manifest blobs',
  'kv.testRestoreStatus': 'Test restore status',
  'kv.testRestoreVerifiedTargets': 'Test-restore verified targets',
  'kv.trustedBaselineId': 'Trusted baseline ID',
  'action.approveOnce': 'Approve Once',
  'action.approving': 'Approving…',
  'action.reject': 'Reject',
  'action.rejecting': 'Rejecting…',
  'action.sendingApproval': 'Sending approval…',
  'action.sendingRejection': 'Sending rejection…',
  'action.note':
    'One-time action bound to the latest authoritative read. Approval never activates operations, creates checkpoints, or changes policy; rejection is terminal. The view always re-reads the server state afterwards.',
  'feedback.sessionUnavailable': 'Session unavailable — the action was not confirmed.',
  'feedback.notFound': 'This supervision session no longer exists.',
  'feedback.conflict':
    'Action not applied: the authoritative state has moved on. The latest state has been reloaded.',
  'feedback.invalid': 'The action request was rejected as invalid.',
  'feedback.unavailable':
    'Authoritative state is temporarily unavailable. The latest readable state has been reloaded.',
  'feedback.unconfirmed':
    'The action was not confirmed. The latest authoritative state has been reloaded.',
  'feedback.unknown':
    'The action result is unknown. The latest authoritative state has been reloaded.',
};

const zhCN: Messages = {
  'app.brand.name': 'AgentState Guard',
  'app.brand.sub': 'R4 运营控制台',
  'app.nav.primary': '主导航',
  'nav.home': '首页',
  'nav.runtime': '运行环境',
  'nav.agents': '智能体',
  'nav.supervision': '监管',
  'nav.changes': '变更',
  'nav.recovery': '恢复',
  'nav.devices': '设备',
  'nav.aiMonitor': 'AI 监管',
  'nav.settings': '设置',
  'view.loading': '正在加载{label}…',
  'view.unavailable': '{label}暂不可用',
  'view.apiFailed': 'API 请求失败',
  'view.retry': '重试',
  'view.sessionUnavailableBody': '无法与本地后端建立会话，未显示任何权威数据。',
  'banner.sessionUnavailable': '无法与本地后端建立会话。',
  'banner.readinessDegraded': '后端报告就绪状态降级（HTTP 503）',
  'banner.readinessFailed': '就绪检查失败',
  'banner.degradedSuffix': '权威视图可能不可用或已降级。',
  'evidence.none': '暂无证据引用',
  'evidence.toggle': '证据（{count} 条）',
  'settings.language': '语言',
  'settings.languageNote': '更改立即生效。',
  'settings.preferenceNote': '语言仅为显示偏好，不会改变任何权限、安全或证据状态。',
  'language.system': '跟随系统',
  'language.en-US': 'English',
  'language.zh-CN': '简体中文',
  'common.yes': '是',
  'common.no': '否',
  'common.items': '{count} 条记录',
  'state.degraded.title': '{label}视图已降级',
  'state.degraded.body':
    '后端无法投影权威的{label}状态（{reasonCode}）。下方内容均非权威投影。',
  'state.unknown.title': '{label}状态未知',
  'state.unknown.body': '后端报告该视图为 UNKNOWN（{reasonCode}），请勿视为健康。',
  'home.subtitle': '四个只读视图的权威状态总览。',
  'home.section.overview': '总览',
  'changes.unavailable.title': '暂无权威变更数据源',
  'changes.unavailable.detail': '此构建未提供权威变更投影，宁可留空也不猜测。',
  'aiMonitor.unavailable.title': 'AI 监管未配置',
  'aiMonitor.unavailable.detail': '此桌面端未配置 AI 提供方；不执行任何分析，也不推断任何权限。',
  'runtime.empty.title': '暂无运行环境记录',
  'runtime.empty.detail': '后端没有此视图的已验证运行环境事实。',
  'runtime.unknownType': '未知运行环境',
  'runtime.uncertain': '不确定',
  'runtime.noCapabilities': '未报告',
  'agents.empty.title': '未检测到智能体',
  'agents.empty.detail': '后端没有已验证的智能体检测记录。',
  'supervision.empty.title': '暂无监管会话',
  'supervision.empty.detail': '后端没有监管会话记录。',
  'supervision.noDecision': '无决策',
  'supervision.aiAssessmentNone': '暂无 AI 评估',
  'supervision.aiDecision': '决策',
  'supervision.aiSeverity': '严重度',
  'supervision.recoveryFacts': '权威恢复事实',
  'supervision.note':
    '操作仅限对等待人工审批的会话执行一次性的 Approve Once / Reject，并绑定最新权威读取。其余内容保持只读：不激活操作、不创建检查点、不变更策略。',
  'recovery.empty.title': '暂无恢复检查点',
  'recovery.empty.detail': '后端没有检查点记录，恢复级别为 R0。',
  'recovery.degraded.title': '恢复视图已降级 —— 按不可恢复处理',
  'recovery.degraded.body': '后端无法投影权威恢复状态（{reasonCode}），无法确认可恢复性。',
  'recovery.unknown.title': '恢复状态未知 —— 按不可恢复处理',
  'recovery.unknown.body': '后端报告该视图为 UNKNOWN（{reasonCode}），无法确认可恢复性。',
  'recovery.r0.title': '无已验证恢复（R0）—— 按不可恢复处理',
  'recovery.r0.body': '当前没有检查点达到已验证恢复级别，请将恢复能力视为不可用。',
  'recovery.section.level': '恢复级别',
  'recovery.section.baseline': '可信基线',
  'recovery.section.checkpoints': '检查点',
  'recovery.chain.ariaLabel': '恢复级别链 R0 至 R3',
  'recovery.chain.current': '当前级别',
  'recovery.chain.verified': '已验证',
  'recovery.chain.notVerified': '未验证',
  'recovery.chain.currentSuffix': ' · 当前',
  'recovery.baseline.note': '可信基线是独立的信任状态，恢复级别 R3 不代表 TRUSTED。',
  'recovery.card.title': '检查点 {id}',
  'recovery.card.failClosed':
    '安全关闭：此检查点没有已验证恢复级别（{reasonCode}），请勿视为可恢复。',
  'kv.executionDomain': '执行域',
  'kv.capabilities': '能力',
  'kv.role': '角色',
  'kv.confidence': '置信度',
  'kv.workspaceStatus': '工作区状态',
  'kv.workspaceBinding': '工作区绑定',
  'kv.requiresManualApproval': '需要人工审批',
  'kv.manualApprovalGranted': '人工审批已授予',
  'kv.requiresCheckpoint': '需要检查点',
  'kv.aiAssessment': 'AI 评估',
  'kv.recoveryLevel': '恢复级别',
  'kv.r1Verified': 'R1 已验证',
  'kv.r2Verified': 'R2 已验证',
  'kv.r3Verified': 'R3 已验证',
  'kv.testRestore': '测试恢复',
  'kv.trustedBaseline': '可信基线',
  'kv.baselineId': '基线 ID',
  'kv.requestedTargets': '请求目标数',
  'kv.authorizedSnapshotTargets': '已授权快照目标',
  'kv.intactManifestBlobs': '完整清单数据块',
  'kv.testRestoreStatus': '测试恢复状态',
  'kv.testRestoreVerifiedTargets': '测试恢复已验证目标',
  'kv.trustedBaselineId': '可信基线 ID',
  'action.approveOnce': '批准一次',
  'action.approving': '正在批准…',
  'action.reject': '拒绝',
  'action.rejecting': '正在拒绝…',
  'action.sendingApproval': '正在发送批准…',
  'action.sendingRejection': '正在发送拒绝…',
  'action.note':
    '一次性操作，绑定最新权威读取。批准不会激活操作、创建检查点或变更策略；拒绝为终态。视图始终会重新读取服务端状态。',
  'feedback.sessionUnavailable': '会话不可用 —— 操作未被确认。',
  'feedback.notFound': '该监管会话已不存在。',
  'feedback.conflict': '操作未应用：权威状态已变化，已重新加载最新状态。',
  'feedback.invalid': '操作请求被拒绝：请求无效。',
  'feedback.unavailable': '权威状态暂时不可用，已重新加载可读取的最新状态。',
  'feedback.unconfirmed': '操作未被确认，已重新加载最新权威状态。',
  'feedback.unknown': '操作结果未知，已重新加载最新权威状态。',
};

export const MESSAGES: Record<Locale, Messages> = {
  'en-US': enUS,
  'zh-CN': zhCN,
};
