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
  | 'nav.environment'
  | 'nav.runtime'
  | 'nav.agents'
  | 'nav.supervision'
  | 'nav.changes'
  | 'nav.recovery'
  | 'nav.devices'
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
  | 'common.close'
  | 'state.degraded.title'
  | 'state.degraded.body'
  | 'state.unknown.title'
  | 'state.unknown.body'
  | 'home.subtitle'
  | 'home.section.overview'
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
  | 'recovery.section.scope'
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
  | 'feedback.unknown'
  | 'devices.section.mobile'
  | 'devices.unpaired.title'
  | 'devices.unpaired.detail'
  | 'devices.addDevice'
  | 'devices.unavailable'
  | 'devices.firstHint'
  | 'devices.pairing.title'
  | 'devices.qr.awaiting'
  | 'devices.identity'
  | 'devices.expires'
  | 'devices.waiting'
  | 'devices.sas.prompt'
  | 'devices.sas.confirm'
  | 'devices.sas.reject'
  | 'devices.confirming'
  | 'devices.paired.title'
  | 'devices.paired.detail'
  | 'devices.expired'
  | 'devices.rejected.title'
  | 'devices.error.title'
  | 'devices.cancel'
  | 'devices.done'
  | 'devices.startOver'
  /* ---- environment / doctor / analyze ---- */
  | 'env.title'
  | 'env.section.runtimeAgents'
  | 'env.section.doctor'
  | 'env.section.status'
  | 'env.section.runtime'
  | 'env.section.agents'
  | 'env.status.versions'
  | 'env.status.checks'
  | 'env.refreshFailed'
  | 'env.observedAt'
  | 'env.refresh'
  | 'env.refreshing'
  | 'env.refreshDone'
  | 'analyze.action'
  | 'analyze.running'
  | 'analyze.notConfigured'
  | 'analyze.failed'
  | 'analyze.title'
  | 'analyze.severity'
  | 'analyze.uncertainties'
  | 'analyze.checks'
  | 'analyze.provider'
  | 'analyze.model'
  | 'analyze.analyzedAt'
  /* ---- changes / evidence ---- */
  | 'changes.section.recent'
  | 'changes.empty.title'
  | 'changes.empty.detail'
  | 'changes.detail.title'
  | 'changes.field.policy'
  | 'changes.field.approval'
  | 'changes.openEvidence'
  | 'changes.col.time'
  | 'changes.col.result'
  | 'changes.col.actor'
  | 'changes.col.subject'
  | 'changes.col.checkpoint'
  | 'evidence.detail.title'
  | 'evidence.detail.empty'
  | 'evidence.field.eventId'
  | 'evidence.field.type'
  | 'evidence.field.result'
  | 'evidence.field.actor'
  | 'evidence.field.subject'
  | 'evidence.field.checkpoint'
  | 'evidence.field.change'
  | 'evidence.field.session'
  | 'evidence.field.verification'
  | 'evidence.field.status'
  | 'evidence.field.recordedAt'
  | 'evidence.field.source'
  | 'evidence.field.chainRef'
  | 'evidence.field.affectedObjects'
  | 'evidence.related'
  /* ---- controlled change ---- */
  | 'cc.title'
  | 'cc.load'
  | 'cc.loadHint'
  | 'cc.loading'
  | 'cc.containerTitle'
  | 'cc.intentTitle'
  | 'cc.intent'
  | 'cc.decisionTitle'
  | 'cc.decision'
  | 'cc.reasonCode'
  | 'cc.requiresApproval'
  | 'cc.requiresCheckpoint'
  | 'cc.actionRef'
  | 'cc.applyTitle'
  | 'cc.apply'
  | 'cc.applyDisabledHint'
  | 'cc.approving'
  | 'cc.applyRunning'
  | 'cc.resultTitle'
  | 'cc.changed'
  | 'cc.verification'
  | 'cc.rolledBack'
  | 'cc.checkpointId'
  | 'cc.beforeDigest'
  | 'cc.afterDigest'
  | 'cc.statusTitle'
  | 'cc.cancel'
  | 'cc.startOver'
  | 'cc.contentPlaceholder'
  | 'cc.section'
  | 'cc.prepare'
  | 'cc.preparing'
  | 'cc.preparedTitle'
  | 'cc.applyApprovedHint'
  | 'cc.applyDoneTitle'
  | 'cc.reset'
  | 'supervision.field.pendingApproval'
  | 'supervision.field.blockedReason'
  | 'supervision.field.latestCheckpoint'
  | 'supervision.field.confirmedResult'
  | 'supervision.section.activity'
  | 'supervision.field.createdAt'
  | 'supervision.field.updatedAt'
  | 'supervision.section.observedAgents'
  /* ---- devices ---- */
  | 'devices.section.binding'
  | 'devices.enable'
  | 'devices.disable'
  | 'devices.refresh'
  | 'devices.enabled'
  | 'devices.disabled'
  | 'devices.endpoint'
  | 'devices.address'
  | 'devices.subnet'
  | 'devices.desktopUuid'
  | 'devices.signFp'
  | 'devices.tlsFp'
  | 'devices.boundTitle'
  | 'devices.noBound'
  | 'devices.lastSeen'
  | 'devices.activeSessions'
  | 'devices.revoke'
  | 'devices.revoking'
  | 'devices.revoked'
  | 'devices.qrHint'
  | 'devices.sas.label'
  | 'devices.sasWaiting'
  | 'kv.observedAt'
  | 'kv.lastSeenAt'
  | 'kv.expiresAt'
  | 'kv.agentCount'
  | 'kv.runtimeCount'
  /* ---- settings / AI ---- */
  | 'settings.section.ai'
  | 'settings.provider.baseUrl'
  | 'settings.provider.apiKey'
  | 'settings.provider.model'
  | 'settings.provider.fetchModels'
  | 'settings.provider.fetching'
  | 'settings.provider.test'
  | 'settings.provider.testing'
  | 'settings.provider.ok'
  | 'settings.provider.failed'
  | 'settings.provider.modelCount'
  | 'settings.provider.note'
  | 'ai.advisory.none'
  /* ---- supervised analyze surface ---- */
  | 'supervision.section.analyze'
  | 'env.section.analyze'
  | 'home.deviceLink'
  | 'home.section.advisory'
  /* ---- recovery actions ---- */
  | 'recovery.create'
  | 'recovery.create.running'
  | 'recovery.create.done'
  | 'recovery.test'
  | 'recovery.test.running'
  | 'recovery.restore'
  | 'recovery.restore.running'
  | 'recovery.restore.confirmTitle'
  | 'recovery.restore.confirmBody'
  | 'recovery.restore.confirmType'
  | 'recovery.action.receipt'
  | 'recovery.action.failed'
  /* ---- device link lifecycle ---- */
  | 'devices.disableNote'
  | 'devices.actionFailed'
  | 'devices.enableRunning'
  | 'devices.disableRunning'
  | 'devices.refreshRunning'
  | 'devices.firewall.state'
  | 'devices.firewall.stateApplied'
  | 'devices.firewall.stateIdle'
  | 'devices.firewall.stateAttention'
  | 'devices.firewall.stateUnknown'
  | 'devices.firewall.diagnostics';

export type Messages = Record<MessageKey, string>;

const enUS: Messages = {
  'app.brand.name': 'AgentState Guard',
  'app.brand.sub': 'AI agent runtime console',
  'app.nav.primary': 'Primary',
  'nav.home': 'Home',
  'nav.environment': 'Environment',
  'nav.runtime': 'Runtime',
  'nav.agents': 'Agents',
  'nav.supervision': 'Supervision',
  'nav.changes': 'Changes',
  'nav.recovery': 'Recovery',
  'nav.devices': 'Devices',
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
  'common.close': 'Close',
  'state.degraded.title': '{label} view degraded',
  'state.degraded.body':
    'The backend could not project authoritative {label} state right now. Nothing below is projected authority.',
  'state.unknown.title': '{label} state unknown',
  'state.unknown.body':
    'The backend reports this view as UNKNOWN. Do not treat it as healthy.',
  'home.subtitle': 'Authoritative state across the four read views.',
  'home.section.overview': 'Overview',
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
    'The backend could not project authoritative recovery state. Recoverability cannot be confirmed.',
  'recovery.unknown.title': 'Recovery state unknown — fail closed',
  'recovery.unknown.body':
    'The backend reports this view as UNKNOWN. Recoverability cannot be confirmed.',
  'recovery.r0.title': 'No verified recovery (R0) — fail closed',
  'recovery.r0.body':
    'No checkpoint currently meets a verified recovery level. Treat restore capability as unavailable.',
  'recovery.section.scope': 'Recovery scope and verification',
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
  'devices.unpaired.title': 'No mobile device connected',
  'devices.unpaired.detail': 'Pair a phone to link it with this desktop.',
  'devices.section.mobile': 'Mobile Devices',
  'devices.addDevice': 'Add mobile device',
  'devices.unavailable':
    'Device Link is disabled or its authority is unavailable — pairing cannot start until the link is enabled above.',
  'devices.firstHint':
    'The mobile shows a 6-digit security code during pairing; confirm the same code appears here.',
  'devices.pairing.title': 'Pair a mobile device',
  'devices.qr.awaiting': 'QR payload pending…',
  'devices.identity': 'Desktop identity',
  'devices.expires': 'Pairing offer expires in {seconds}s',
  'devices.waiting': 'Waiting for the mobile device…',
  'devices.sas.prompt':
    'Both devices must confirm the same security code. Verify that the code below is identical on the paired mobile device.',
  'devices.sas.label': 'Security code',
  'devices.sas.confirm': 'Codes match',
  'devices.sas.reject': 'Codes do not match — cancel',
  'devices.confirming': 'Confirming…',
  'devices.paired.title': 'Device paired',
  'devices.paired.detail': 'The mobile device is now linked with this desktop.',
  'devices.expired': 'The pairing offer expired.',
  'devices.rejected.title': 'Pairing rejected',
  'devices.error.title': 'Pairing failed',
  'devices.cancel': 'Cancel',
  'devices.done': 'Done',
  'devices.startOver': 'Start over',
  'env.title': 'Environment',
  'env.section.runtimeAgents': 'Runtime & Agents',
  'env.section.doctor': 'Doctor',
  'env.section.status': 'Status',
  'env.section.runtime': 'Runtime',
  'env.section.agents': 'Agents',
  'env.status.versions': 'Versions',
  'env.status.checks': 'Checks',
  'env.refreshFailed': 'Discovery refresh failed; the currently shown state is unchanged.',
  'env.observedAt': 'observed at {time}',
  'env.refresh': 'Refresh discovery',
  'env.refreshing': 'Refreshing…',
  'env.refreshDone': 'Refreshed.',
  'analyze.action': 'Analyze Current Environment',
  'analyze.running': 'Analyzing…',
  'analyze.notConfigured': 'AI provider is not configured. Configure and test one in Settings.',
  'analyze.failed': 'Analysis failed ({reasonCode})',
  'analyze.title': 'AI Advisory',
  'analyze.severity': 'severity',
  'analyze.uncertainties': 'Uncertainties',
  'analyze.checks': 'Recommended checks',
  'analyze.provider': 'provider',
  'analyze.model': 'model',
  'analyze.analyzedAt': 'analyzed at {time}',
  'changes.section.recent': 'Verified activity',
  'changes.empty.title': 'No verified activity',
  'changes.empty.detail': 'The verified Ledger holds no activity entries for this feed.',
  'changes.detail.title': 'Selected activity',
  'changes.field.policy': 'Policy summary',
  'changes.field.approval': 'Approval summary',
  'changes.openEvidence': 'Evidence',
  'changes.col.time': 'Time',
  'changes.col.result': 'Result',
  'changes.col.actor': 'Actor',
  'changes.col.subject': 'Subject',
  'changes.col.checkpoint': 'Checkpoint',
  'evidence.detail.title': 'Evidence detail',
  'evidence.detail.empty': 'Select an evidence reference to inspect its sanitized detail.',
  'evidence.field.eventId': 'Event ID',
  'evidence.field.type': 'Type',
  'evidence.field.result': 'Result',
  'evidence.field.actor': 'Actor',
  'evidence.field.subject': 'Subject',
  'evidence.field.checkpoint': 'Checkpoint',
  'evidence.field.change': 'Change',
  'evidence.field.session': 'Session',
  'evidence.field.verification': 'Verification',
  'evidence.field.status': 'Status',
  'evidence.field.recordedAt': 'recorded_at',
  'evidence.field.source': 'Source',
  'evidence.field.chainRef': 'chain_ref',
  'evidence.field.affectedObjects': 'Affected objects',
  'evidence.related': 'Related evidence',
  'cc.title': 'Controlled Change',
  'cc.load': 'Load config',
  'cc.loadHint': 'Load the current TOML from the backend (read-only fetch).',
  'cc.loading': 'Loading…',
  'cc.containerTitle': 'Edit TOML',
  'cc.intentTitle': 'Prepare',
  'cc.intent': 'How the backend judged this content (decision is authoritative).',
  'cc.decisionTitle': 'Backend decision',
  'cc.decision': 'Decision',
  'cc.reasonCode': 'reason_code',
  'cc.requiresApproval': 'Requires manual approval',
  'cc.requiresCheckpoint': 'Requires checkpoint',
  'cc.actionRef': 'action_ref',
  'cc.applyTitle': 'Apply',
  'cc.apply': 'Apply controlled change',
  'cc.applyDisabledHint': 'Apply is enabled only when the backend issued action_ref and manual approval is required.',
  'cc.approving': 'Approving…',
  'cc.applyRunning': 'Applying…',
  'cc.resultTitle': 'Apply result (authoritative)',
  'cc.changed': 'changed',
  'cc.verification': 'Verification',
  'cc.rolledBack': 'rolled_back',
  'cc.checkpointId': 'checkpoint_id',
  'cc.beforeDigest': 'before digest',
  'cc.afterDigest': 'after digest',
  'cc.statusTitle': 'Status',
  'cc.cancel': 'Cancel',
  'cc.startOver': 'Start over',
  'cc.contentPlaceholder': '# TOML content to prepare/apply',
  'cc.section': 'Controlled Change',
  'cc.prepare': 'Prepare change',
  'cc.preparing': 'Preparing…',
  'cc.preparedTitle': 'Prepared session (backend verdict)',
  'cc.applyApprovedHint': 'Approve once above, then apply the identical content.',
  'cc.applyDoneTitle': 'Apply result (authoritative)',
  'cc.reset': 'Start over',
  'supervision.field.pendingApproval': 'Pending approval',
  'supervision.field.blockedReason': 'Blocked/failed reason',
  'supervision.field.latestCheckpoint': 'Latest checkpoint',
  'supervision.field.confirmedResult': 'Latest confirmed result',
  'supervision.section.activity': 'Recent verified activity',
  'supervision.field.createdAt': 'created_at',
  'supervision.field.updatedAt': 'updated_at',
  'supervision.section.observedAgents': 'Observed agents (backend projection)',
  'devices.section.binding': 'Device Link',
  'devices.enable': 'Enable',
  'devices.disable': 'Disable',
  'devices.refresh': 'Refresh network',
  'devices.enabled': 'ENABLED',
  'devices.disabled': 'DISABLED',
  'devices.endpoint': 'Endpoint',
  'devices.address': 'Address',
  'devices.subnet': 'Subnet',
  'devices.desktopUuid': 'Desktop UUID',
  'devices.signFp': 'Signing fingerprint',
  'devices.tlsFp': 'TLS SPKI fingerprint',
  'devices.boundTitle': 'Bound devices',
  'devices.noBound': 'No mobile device is currently bound.',
  'devices.lastSeen': 'last seen {time}',
  'devices.activeSessions': 'active pair sessions',
  'devices.revoke': 'Revoke',
  'devices.revoking': 'Revoking…',
  'devices.revoked': 'Revoked.',
  'devices.qrHint': 'Scan with the Android app. The QR contains the full canonical invitation.',
  'devices.sasWaiting': 'Waiting for the first connection from the mobile device…',
  'kv.observedAt': 'observed_at',
  'kv.lastSeenAt': 'last_seen',
  'kv.expiresAt': 'expires_at',
  'kv.agentCount': 'agents',
  'kv.runtimeCount': 'runtime',
  'settings.section.ai': 'AI Provider',
  'settings.provider.baseUrl': 'Base URL',
  'settings.provider.apiKey': 'API key',
  'settings.provider.model': 'Model',
  'settings.provider.fetchModels': 'Fetch models',
  'settings.provider.fetching': 'Fetching…',
  'settings.provider.test': 'Test connection',
  'settings.provider.testing': 'Testing…',
  'settings.provider.ok': 'Connected ({models} models, {latency}ms)',
  'settings.provider.failed': 'Test failed: {error}',
  'settings.provider.modelCount': '{count} models found',
  'settings.provider.note':
    'The API key is held in memory only. It is never written to disk, never echoed back, and never sent to the Android app.',
  'ai.advisory.none': 'No advisory yet.',
  'supervision.section.analyze': 'AI Advisory',
  'env.section.analyze': 'AI Advisory',
  'home.deviceLink': 'Device Link',
  'home.section.advisory': 'Latest advisory',
  'recovery.create': 'Create Checkpoint',
  'recovery.create.running': 'Creating…',
  'recovery.create.done': 'Created ({status}) · checkpoint {id} — created ≠ recoverable',
  'recovery.test': 'Test Restore',
  'recovery.test.running': 'Testing…',
  'recovery.restore': 'Restore',
  'recovery.restore.running': 'Restoring…',
  'recovery.restore.confirmTitle': 'Confirm restore',
  'recovery.restore.confirmBody':
    'Restore writes the server-owned product config target of checkpoint {id}. This is the only restore scope.',
  'recovery.restore.confirmType': 'Type {word} to confirm',
  'recovery.action.receipt': 'Action receipt:',
  'recovery.action.failed': 'Action failed ({reasonCode}); the authoritative state shown was left unchanged.',
  'devices.disableNote':
    'Disable removes exposure but keeps the durable binding; it is not unpair. Revoke removes the binding.',
  'devices.actionFailed': 'Action failed ({reasonCode})',
  'devices.enableRunning': 'Enabling…',
  'devices.disableRunning': 'Disabling…',
  'devices.refreshRunning': 'Refreshing…',
  'devices.firewall.state': 'Firewall',
  'devices.firewall.stateApplied': 'Protection applied',
  'devices.firewall.stateIdle': 'Not applied',
  'devices.firewall.stateAttention': 'Needs attention',
  'devices.firewall.stateUnknown': 'Status unavailable',
  'devices.firewall.diagnostics': 'Firewall diagnostics',
};

const zhCN: Messages = {
  'app.brand.name': 'AgentState Guard',
  'app.brand.sub': 'AI Agent 运行监管台',
  'app.nav.primary': '主导航',
  'nav.home': '首页',
  'nav.environment': '环境',
  'nav.runtime': '运行环境',
  'nav.agents': '智能体',
  'nav.supervision': '监管',
  'nav.changes': '变更',
  'nav.recovery': '恢复',
  'nav.devices': '设备',
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
  'common.close': '关闭',
  'state.degraded.title': '{label}视图已降级',
  'state.degraded.body': '后端暂时无法投影权威的{label}状态。下方内容均非权威投影。',
  'state.unknown.title': '{label}状态未知',
  'state.unknown.body': '后端报告该视图为 UNKNOWN，请勿视为健康。',
  'home.subtitle': '四个只读视图的权威状态总览。',
  'home.section.overview': '总览',
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
  'recovery.degraded.body': '后端暂时无法投影权威恢复状态，无法确认可恢复性。',
  'recovery.unknown.title': '恢复状态未知 —— 按不可恢复处理',
  'recovery.unknown.body': '后端报告该视图为 UNKNOWN，无法确认可恢复性。',
  'recovery.r0.title': '无已验证恢复（R0）—— 按不可恢复处理',
  'recovery.r0.body': '当前没有检查点达到已验证恢复级别，请将恢复能力视为不可用。',
  'recovery.section.scope': '恢复范围与验证',
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
  'devices.unpaired.title': '尚未连接手机',
  'devices.unpaired.detail': '配对手机后，即可与此桌面端连接。',
  'devices.section.mobile': '移动设备',
  'devices.addDevice': '添加移动设备',
  'devices.unavailable': 'Device Link 未启用或其权威不可用——请先在上方启用连接后再开始配对。',
  'devices.firstHint': '配对时手机端会显示 6 位安全码；确认此界面出现相同的安全码。',

  'devices.pairing.title': '配对移动设备',
  'devices.qr.awaiting': '正在生成二维码…',
  'devices.identity': '桌面端标识',
  'devices.expires': '配对请求将在 {seconds} 秒后过期',
  'devices.waiting': '等待移动设备连接…',
  'devices.sas.prompt': '两台设备必须确认相同的安全码。请确认下方安全码与配对手机端显示的完全一致。',
  'devices.sas.label': '安全码',
  'devices.sas.confirm': '确认一致',
  'devices.sas.reject': '不一致，取消',
  'devices.confirming': '正在确认…',
  'devices.paired.title': '设备已配对',
  'devices.paired.detail': '移动设备已与此桌面端完成配对。',
  'devices.expired': '配对请求已过期。',
  'devices.rejected.title': '配对已拒绝',
  'devices.error.title': '配对失败',
  'devices.cancel': '取消',
  'devices.done': '完成',
  'devices.startOver': '重新开始',
  'evidence.field.eventId': '事件 ID',
  'evidence.field.type': '类型',
  'evidence.field.result': '结果',
  'evidence.field.actor': '操作者',
  'evidence.field.subject': '对象',
  'evidence.field.checkpoint': '检查点',
  'evidence.field.change': '变更',
  'evidence.field.session': '会话',
  'evidence.field.verification': '验证摘要',
  'evidence.field.status': '状态',
  'evidence.field.recordedAt': 'recorded_at',
  'evidence.field.source': '来源',
  'evidence.field.chainRef': 'chain_ref',
  'evidence.field.affectedObjects': '受影响对象',
  'evidence.related': '相关证据',
  'evidence.detail.title': '证据详情',
  'evidence.detail.empty': '选择一个证据引用查看其受限详情。',
  'env.title': '环境',
  'env.section.runtimeAgents': '运行环境与智能体',
  'env.section.doctor': 'Doctor',
  'env.section.status': '状态',
  'env.section.runtime': '运行环境',
  'env.section.agents': '智能体',
  'env.status.versions': '版本',
  'env.status.checks': '检查项',
  'env.refreshFailed': '发现刷新失败；当前显示状态保持不变。',
  'env.observedAt': '观测于 {time}',
  'env.refresh': '刷新发现',
  'env.refreshing': '刷新中…',
  'env.refreshDone': '已刷新。',
  'analyze.action': '分析当前环境',
  'analyze.running': '分析中…',
  'analyze.notConfigured': 'AI provider 未配置。请先在“设置”中配置并测试。',
  'analyze.failed': '分析失败（{reasonCode}）',
  'analyze.title': 'AI 建议',
  'analyze.severity': '严重度',
  'analyze.uncertainties': '不确定项',
  'analyze.checks': '建议检查',
  'analyze.provider': 'provider',
  'analyze.model': 'model',
  'analyze.analyzedAt': '分析于 {time}',
  'changes.section.recent': '已验证活动',
  'changes.empty.title': '暂无已验证活动',
  'changes.empty.detail': '已验证账本中没有此数据流的活动记录。',
  'changes.detail.title': '选中的活动',
  'changes.field.policy': '策略摘要',
  'changes.field.approval': '审批摘要',
  'changes.openEvidence': '证据',
  'changes.col.time': '时间',
  'changes.col.result': '结果',
  'changes.col.actor': '操作者',
  'changes.col.subject': '对象',
  'changes.col.checkpoint': '检查点',
  'cc.title': '受控变更',
  'cc.load': '加载配置',
  'cc.loadHint': '从后端加载当前 TOML（只读抓取）。',
  'cc.loading': '加载中…',
  'cc.containerTitle': '编辑 TOML',
  'cc.intentTitle': '预备',
  'cc.intent': '后端如何判定这份内容（decision 为权威语义）。',
  'cc.decisionTitle': '后端决断',
  'cc.decision': '决断',
  'cc.reasonCode': 'reason_code',
  'cc.requiresApproval': '需要人工审批',
  'cc.requiresCheckpoint': '需要检查点',
  'cc.actionRef': 'action_ref',
  'cc.applyTitle': '应用',
  'cc.apply': '应用受控变更',
  'cc.applyDisabledHint': '仅当后端下发 action_ref 且需要人工审批时才可应用。',
  'cc.approving': '正在批准…',
  'cc.applyRunning': '正在应用…',
  'cc.resultTitle': '应用结果（权威）',
  'cc.changed': '已变更',
  'cc.verification': '验证',
  'cc.rolledBack': '已回滚',
  'cc.checkpointId': 'checkpoint_id',
  'cc.beforeDigest': 'before digest',
  'cc.afterDigest': 'after digest',
  'cc.statusTitle': '状态',
  'cc.cancel': '取消',
  'cc.startOver': '重新开始',
  'cc.contentPlaceholder': '# 填写要 prepare/apply 的 TOML 内容',
  'cc.section': '受控变更',
  'cc.prepare': '预备变更',
  'cc.preparing': '预备中…',
  'cc.preparedTitle': '已预备会话（后端裁决）',
  'cc.applyApprovedHint': '在上方批准后，再提交相同内容执行应用。',
  'cc.applyDoneTitle': '应用结果（权威）',
  'cc.reset': '重新开始',
  'supervision.field.pendingApproval': '待审批',
  'supervision.field.blockedReason': '阻断/失败原因',
  'supervision.field.latestCheckpoint': '最新检查点',
  'supervision.field.confirmedResult': '最近确认结果',
  'supervision.section.activity': '最近已验证活动',
  'supervision.field.createdAt': 'created_at',
  'supervision.field.updatedAt': 'updated_at',
  'supervision.section.observedAgents': '后端投影的已观测智能体',
  'devices.section.binding': '设备连接',
  'devices.enable': '启用',
  'devices.disable': '停用',
  'devices.refresh': '刷新网络',
  'devices.enabled': '已启用',
  'devices.disabled': '已停用',
  'devices.endpoint': 'Endpoint',
  'devices.address': '地址',
  'devices.subnet': '子网',
  'devices.desktopUuid': '桌面端 UUID',
  'devices.signFp': '签名指纹',
  'devices.tlsFp': 'TLS SPKI 指纹',
  'devices.boundTitle': '已绑定设备',
  'devices.noBound': '当前没有绑定的移动设备。',
  'devices.lastSeen': '上次在线 {time}',
  'devices.activeSessions': '活动配对会话数',
  'devices.revoke': '解除绑定',
  'devices.revoking': '解除中…',
  'devices.revoked': '已解除。',
  'devices.qrHint': '使用 Android 应用扫描。二维码包含完整 canonical 邀请。',
  'devices.sasWaiting': '等待手机端首次连接…',
  'kv.observedAt': 'observed_at',
  'kv.lastSeenAt': 'last_seen',
  'kv.expiresAt': 'expires_at',
  'kv.agentCount': 'agents',
  'kv.runtimeCount': 'runtime',
  'settings.section.ai': 'AI 提供方',
  'settings.provider.baseUrl': 'Base URL',
  'settings.provider.apiKey': 'API key',
  'settings.provider.model': 'Model',
  'settings.provider.fetchModels': '获取模型列表',
  'settings.provider.fetching': '获取中…',
  'settings.provider.test': '测试连接',
  'settings.provider.testing': '测试中…',
  'settings.provider.ok': '已连接（{models} 个模型，{latency}ms）',
  'settings.provider.failed': '测试失败：{error}',
  'settings.provider.modelCount': '发现 {count} 个模型',
  'settings.provider.note': 'API key 仅存内存，不落盘、不回显、不会发送到 Android 应用。',
  'ai.advisory.none': '暂无建议。',
  'supervision.section.analyze': 'AI 建议',
  'env.section.analyze': 'AI 建议',
  'home.deviceLink': '设备连接',
  'home.section.advisory': '最新建议',
  'recovery.create': '创建检查点',
  'recovery.create.running': '正在创建…',
  'recovery.create.done': '已创建（{status}）· 检查点 {id} —— 已创建 ≠ 可恢复',
  'recovery.test': '测试恢复',
  'recovery.test.running': '正在测试…',
  'recovery.restore': '恢复',
  'recovery.restore.running': '正在恢复…',
  'recovery.restore.confirmTitle': '确认恢复',
  'recovery.restore.confirmBody': '恢复只会写回服务端拥有的产品配置目标（检查点 {id}），这是唯一的恢复范围。',
  'recovery.restore.confirmType': '输入 {word} 以确认',
  'recovery.action.receipt': '操作回执：',
  'recovery.action.failed': '操作失败（{reasonCode}），显示中的权威状态保持不变。',
  'devices.disableNote': '停用会移除网络暴露但保留持久绑定，不等于解除配对；解除绑定需使用 Revoke。',
  'devices.actionFailed': '操作失败（{reasonCode}）',
  'devices.enableRunning': '正在启用…',
  'devices.disableRunning': '正在停用…',
  'devices.refreshRunning': '正在刷新…',
  'devices.firewall.state': '防火墙',
  'devices.firewall.stateApplied': '防护已应用',
  'devices.firewall.stateIdle': '未应用',
  'devices.firewall.stateAttention': '需要注意',
  'devices.firewall.stateUnknown': '状态不可用',
  'devices.firewall.diagnostics': '防火墙诊断',
};

export const MESSAGES: Record<Locale, Messages> = {
  'en-US': enUS,
  'zh-CN': zhCN,
};
