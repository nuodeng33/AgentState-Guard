/**
 * R4-P8 authoritative read DTO types.
 *
 * Mirrors the frozen backend contract in docs/phases/08-ui-backend-contract.md
 * (schema_version "r4-p8-1"). The UI must render only what the API returns and
 * must never upgrade, infer, or fabricate authoritative state.
 */

export const R4_SCHEMA_VERSION = 'r4-p8-1' as const;

export type ViewName = 'runtime' | 'agents' | 'supervision' | 'recovery' | 'changes';

/** Top-level projection status. UNKNOWN/DEGRADED/UNREACHABLE are never "fine". */
export type ViewStatus = 'EMPTY' | 'UNKNOWN' | 'DEGRADED' | 'AVAILABLE' | 'UNREACHABLE';

export interface R4ViewDto<TItem> {
  schema_version: string;
  view: ViewName;
  status: ViewStatus;
  reason_code: string;
  evidence_refs: string[];
  items: TItem[];
  /** Latest item observed_at, supplied by the backend projection when available. */
  observed_at?: string;
}

/* ---- Runtime ---- */

export type RuntimeAvailability = 'AVAILABLE' | 'UNKNOWN' | 'UNREACHABLE';

export interface RuntimeItem {
  runtime_type: string | null;
  execution_domain_id: string | null;
  availability: RuntimeAvailability;
  capabilities: string[];
  reason_code: string;
  uncertainty: boolean;
  observed_at?: string | null;
  evidence_refs: string[];
}

export type RuntimeView = R4ViewDto<RuntimeItem>;

/* ---- Agents ---- */

export interface AgentWorkspace {
  status: string;
  binding_ref: string | null;
}

export interface AgentItem {
  detected_identity: string;
  /**
   * Bounded backend instance label (e.g. "KIMI_CODE a1b2c3"); rendered as the
   * primary display identity when present so multiple same-product instances
   * stay distinguishable. Never leaks workspace or user paths.
   */
  instance_label?: string | null;
  role: string;
  /** Backend-owned lifecycle (e.g. DETECTED | UNKNOWN). Rendered verbatim. */
  lifecycle: string;
  confidence: number;
  execution_domain_id: string | null;
  workspace: AgentWorkspace;
  reason_code: string;
  uncertainty: boolean;
  observed_at?: string | null;
  evidence_refs: string[];
}

export type AgentsView = R4ViewDto<AgentItem>;

/* ---- Supervision ---- */

export type PolicyDecision = 'ALLOW' | 'REVIEW' | 'BLOCK' | 'UNKNOWN';

export interface SupervisionAiAssessment {
  decision: string | null;
  severity: string | null;
}

/** Allowlisted recovery facts attached to a POLICY_EVALUATED event. */
export interface SupervisionRecoveryFacts {
  status?: string | null;
  reason_code?: string | null;
  requested_targets?: number | null;
  authorized_snapshot_targets?: number | null;
  intact_manifest_blob_targets?: number | null;
  authorized_snapshot_coverage?: number | null;
  manifest_blob_coverage?: number | null;
  test_restore_verified_targets?: number | null;
  test_restore_status?: string | null;
  recovery_level?: string | null;
  r1_verified?: boolean | null;
  r2_verified?: boolean | null;
  r3_verified?: boolean | null;
  trusted_baseline_status?: string | null;
  trusted_baseline_id?: string | null;
}

export interface SupervisionItem {
  supervision_session_id: string;
  /** Backend-owned lifecycle. Rendered verbatim; never re-derived by the UI. */
  status: string;
  /** Same as status; backend emits both (r4_projection.supervision). */
  observable_status?: string;
  policy_decision: string | null;
  manual_approval?: boolean | null;
  requires_manual_approval?: boolean | null;
  requires_checkpoint?: boolean | null;
  /** Backend verdict: session awaits manual approval (REVIEW, AWAITING_APPROVAL). */
  pending_approval?: boolean | null;
  /** POLICY_BLOCKED, or SESSION_FAILED/RESTORE_FAILED reason; else null. */
  blocked_or_failed_reason?: string | null;
  /** Advisory only — never merges with policy_decision/status. */
  ai_assessment: SupervisionAiAssessment | null;
  recovery_facts: SupervisionRecoveryFacts | null;
  /** Latest activity terminal result (SESSION_COMPLETED/FAILED/USER_*); else null. */
  recent_confirmed_result?: ChangeItem | null;
  /** Verified OBSERVED_CHANGE activities within this session's feed. */
  recent_changes?: ChangeItem[];
  /** Checkpoint summary bound to this session (from verified activities). */
  latest_checkpoint?: {
    checkpoint_id: string | null;
    reason_code: string;
    evidence_refs: string[];
  } | null;
  /** Backend probes; null when the backend has no authoritative value. */
  current_task?: string | null;
  current_phase?: string | null;
  current_action?: string | null;
  /**
   * Opaque server-issued one-time action binding (64 lowercase hex), present
   * only while a REVIEW session genuinely awaits manual approval. Never
   * generated, stored, or reused across sessions by the client.
   */
  action_ref: string | null;
  evidence_refs: string[];
  /** Session creation timestamp (backend row). Rendered verbatim. */
  created_at?: string | null;
  /** Last session update timestamp (backend row). Rendered verbatim. */
  updated_at?: string | null;
  /** Timestamp of the latest verified activity, or updated_at fallback. */
  observed_at?: string | null;
  /** Latest verified activity entry for this session (from the change feed). */
  latest_verified_activity?: ChangeItem | null;
  /** Up to 20 recent verified activities for this session. */
  recent_verified_activities?: ChangeItem[];
}

/**
 * r4-p8-action-1 mutation acknowledgement. The UI never treats this as
 * authority on its own; the supervision view is always re-read afterwards.
 */
export interface SupervisionActionResult {
  schema_version: string;
  action: string;
  supervision_session_id: string;
  status: string;
  reason_code: string;
  consumed: boolean;
  evidence_refs: string[];
}

export type SupervisionView = R4ViewDto<SupervisionItem> & {
  /**
   * Backend-attached agents projection items (r4_projection.supervision:577).
   * Rendered verbatim like /api/v1/agents items; never re-interpreted.
   */
  observed_agents?: AgentItem[];
  /** Cross-session verified activity feed (up to 50). */
  recent_verified_activities?: ChangeItem[];
};

/* ---- Recovery ---- */

export type RecoveryLevel = 'R0' | 'R1' | 'R2' | 'R3';

/** Trusted Baseline is a separate trust state, never implied by recovery level. */
export type TrustedBaselineStatus = 'NONE' | 'TRUSTED' | 'RETIRED' | 'REVOKED';
export type RecoveryScopeKind = 'HOST_WORKSPACE' | 'PRODUCT_CONFIG' | 'UNKNOWN';

export interface RecoveryCoverageCounts {
  restorable: number | null;
  audit_only: number | null;
  excluded: number | null;
  unreachable: number | null;
}

export interface RecoveryCoverage {
  counts: RecoveryCoverageCounts | null;
  reason_counts: Record<string, number>;
  scan_complete: boolean;
  scan_reason_code: string | null;
}

export interface RecoverySummaryFields {
  recovery_level: RecoveryLevel;
  r1_verified: boolean;
  r2_verified: boolean;
  r3_verified: boolean;
  test_restore_status: string;
  trusted_baseline_status: TrustedBaselineStatus;
  trusted_baseline_id: string | null;
}

export interface RecoveryItem extends RecoverySummaryFields {
  checkpoint_id: string;
  execution_domain_id: string | null;
  status: string;
  reason_code: string;
  requested_targets: number;
  authorized_snapshot_targets: number;
  intact_manifest_blob_targets: number;
  authorized_snapshot_coverage: number | null;
  manifest_blob_coverage: number | null;
  test_restore_verified_targets: number | null;
  evidence_refs: string[];
  created_at?: string | null;
  manifest_integrity?: string | null;
  actual_restore_status: string;
  actual_restore_verified_at: string | null;
  actual_restore_evidence_refs: string[];
  scope_kind: RecoveryScopeKind;
  workspace_id: string | null;
  coverage: RecoveryCoverage | null;
}

export type RecoveryView = R4ViewDto<RecoveryItem> & {
  /** Recovery summary fields are absent on authoritative unavailable/degraded projections. */
  recovery_level?: RecoveryLevel | null;
  r1_verified?: boolean | null;
  r2_verified?: boolean | null;
  r3_verified?: boolean | null;
  test_restore_status?: string | null;
  trusted_baseline_status?: TrustedBaselineStatus | null;
  trusted_baseline_id?: string | null;
  checkpoint_count?: number | null;
  latest_checkpoint?: RecoveryItem | null;
  actual_restore_status?: string | null;
  recovery_verified?: boolean | null;
  verified_at?: string | null;
  capabilities?: { create_checkpoint: boolean; test_restore: boolean; restore: boolean } | null;
  scope_kind?: RecoveryScopeKind | null;
  workspace_id?: string | null;
  coverage?: RecoveryCoverage | null;
  limitations?: string[] | null;
};

/* ---- Changes (r4-p8-1 view=changes) ---- */

export interface ChangeItem {
  event_id: string;
  timestamp: string;
  observed_at: string | null;
  recorded_at: string;
  actor: string;
  subject: string | null;
  type: string;
  result: string;
  affected_objects: string[];
  checkpoint_id: string | null;
  change_id: string | null;
  supervision_session_id: string | null;
  /** Backend result of a POLICY_EVALUATED event; null for other types. */
  policy_summary?: string | null;
  /** Backend result of a USER_APPROVED/USER_REJECTED event; null otherwise. */
  approval_summary?: string | null;
  verification_summary: string | null;
  reason_code: string;
  execution_domain_id: string | null;
  attribution: string | null;
  change_kind: string | null;
  coverage_before: string | null;
  coverage_after: string | null;
  recovery_disposition: string | null;
  workspace_id: string | null;
  evidence_refs: string[];
}

export type ChangesView = R4ViewDto<ChangeItem>;

/* ---- Evidence detail (r4-product-evidence-1) ---- */

export interface EvidenceDetail {
  schema_version: string;
  status: 'AVAILABLE' | 'DEGRADED' | 'NOT_FOUND';
  reason_code: string;
  event_id: string;
  event_type?: string;
  observed_at?: string;
  recorded_at?: string;
  source?: string;
  subject?: string | null;
  result?: string;
  verification_summary?: string | null;
  execution_domain_id?: string | null;
  checkpoint_id?: string | null;
  change_id?: string | null;
  chain_ref?: string;
  sanitized_detail?: {
    affected_objects?: string[];
    verification?: string | null;
    attribution?: string | null;
    change_kind?: string | null;
    coverage_before?: string | null;
    coverage_after?: string | null;
    recovery_disposition?: string | null;
    workspace_id?: string | null;
  };
  related_evidence_refs?: string[];
}

/* ---- Discovery refresh (product-discovery-1) ----
 *
 * Reality-checked against agentguard/api/server.py:285-315 + 563-584: the
 * realtime refresh emits AVAILABLE / DEGRADED (snapshot.status.value), while
 * the request-validation handler (server.py:125-139) and the frozen contract
 * also allow UNCHANGED. The union therefore keeps all three tokens verbatim.
 */

export interface DiscoveryRefreshResult {
  schema_version: string;
  status: 'AVAILABLE' | 'DEGRADED' | 'UNCHANGED';
  reason_code: string;
  snapshot_id: string | null;
  observed_at: string | null;
  affected_views: string[];
  runtime_count: number;
  agent_count: number;
  evidence_refs: string[];
}

/* ---- Recovery actions (product-recovery-action-1) ---- */

export interface RecoveryActionResult {
  schema_version: string;
  status: string;
  reason_code: string;
  checkpoint_id: string | null;
  manifest_digest?: string | null;
  verified_targets?: number | null;
  scope_kind?: RecoveryScopeKind | null;
  workspace_id?: string | null;
  coverage?: RecoveryCoverageCounts | null;
  coverage_reason_counts?: Record<string, number> | null;
  scan_complete?: boolean | null;
  scan_reason_code?: string | null;
  post_restore_status?: string | null;
  quarantined_targets?: number | null;
  residue_targets?: number | null;
  evidence_refs: string[];
}

/* ---- Device Link lifecycle (device-link-lifecycle-1) ---- */

export interface BoundDevice {
  uuid: string;
  fingerprint: string;
  display_name: string;
  last_seen: string | null;
}

export interface DeviceLinkFirewallStatus {
  operation: string;
  status: string;
  reason_code: string;
  scope_digest: string | null;
  recorded_at: string | null;
}

export interface DeviceLinkStatus {
  schema_version: string;
  enabled?: boolean | null;
  status: 'ENABLED' | 'DISABLED' | 'DEGRADED';
  endpoint: string | null;
  address: string | null;
  subnet: string | null;
  reason_code: string;
  desktop_uuid: string | null;
  desktop_signing_fingerprint: string | null;
  tls_spki_fingerprint: string | null;
  firewall?: DeviceLinkFirewallStatus | null;
  bound_devices?: BoundDevice[];
  active_pair_sessions?: number;
}

/* ---- Device Link pairing ---- */

export interface PairingInvitation {
  protocol_version: number;
  session_id: string;
  ticket: string;
  endpoint: string;
  expires_in_s: number;
  expires_at_epoch: number;
  desktop_uuid: string;
  desktop_public_key_der: string;
  desktop_signing_fingerprint: string;
  tls_spki_fingerprint: string;
  state: string;
}

export interface PairingStateResult {
  session_id: string;
  state: 'created' | 'first_connection' | 'sas_pending' | 'confirmed_both' | 'consumed' | 'expired' | 'rejected' | 'failed' | 'cancelled';
  confirmed_by?: string[];
  /**
   * Server-owned, human-formatted six-digit SAS (e.g. "123 456"), projected
   * exclusively while state is `sas_pending`. Terminal phases omit it.
   */
  sas?: string;
}

export interface DeviceLinkActionResult {
  schema_version: string;
  status: string;
  device_uuid?: string;
}

/* ---- AI (product-ai-advisory-1 + models/test) ---- */

export interface AiAdvisory {
  schema_version: string;
  status: 'AVAILABLE' | 'UNAVAILABLE' | 'UNCHANGED';
  reason_code: string;
  severity: string;
  summary: string | null;
  uncertainties: string[];
  recommended_checks: string[];
  evidence_refs: string[];
  provider: string | null;
  model: string | null;
  analyzed_at: string | null;
}

export interface AiTestResult {
  ok: boolean;
  latency_ms?: number;
  models_available?: number;
  message?: string;
  error?: string;
  detail?: string;
}

export interface AiModelsResult {
  models: Array<{ id: string; provider: string }>;
  error?: string;
}

/* ---- Controlled change (r4-p9-controlled-change-1) ---- */

/* ---- Controlled change (r4-p9-controlled-change-1) ----
 *
 * Reality-checked against agentguard/api/r4_controlled_change.py: the
 * prepare response exactly matches prepare_controlled_change's dict, apply
 * matches apply_config_change's result dict. The server owns the sole target
 * (config/agentguard.toml), policy, checkpoint, approval and verification.
 */

export interface ControlledChangePrep {
  schema_version: string;
  supervision_session_id: string;
  /** Backend session status verbatim (e.g. AWAITING_APPROVAL | EVALUATED). */
  status: string;
  /** Unchanged even on BLOCK; the decision carries the verdict. */
  decision: 'ALLOW' | 'REVIEW' | 'BLOCK' | 'UNKNOWN' | string;
  reason_code: string;
  requires_manual_approval: boolean;
  requires_checkpoint: boolean;
  /** Advisory token only: "BYPASSED" | "UNAVAILABLE" | assessment token. */
  ai_advisory: string;
  /** Server-issued one-time binding, 64 lowercase hex or null. */
  action_ref: string | null;
}

export interface ControlledChangeApplyResult {
  schema_version: string;
  supervision_session_id: string;
  status: string;
  reason_code: string;
  changed: boolean;
  verification: string | null;
  rolled_back: boolean | null;
  before_digest: string | null;
  after_digest: string | null;
  checkpoint_id: string | null;
  evidence_refs: string[];
}

/* ---- Status/Doctor (environment surface) ----
 *
 * Reality-checked against agentguard/commands/status.py and
 * agentguard/commands/doctor.py: /api/status returns the bounded status
 * payload (timestamp_utc / checks / versions) and /api/doctor returns a plain
 * list of check rows. The UI renders these verbatim and never derives a
 * verdict absent from the payload.
 */

export interface StatusPayload {
  /** Backend wallclock source timestamp for this status read. */
  timestamp_utc?: string;
  checks?: Record<string, boolean | string | null>;
  versions?: Record<string, string | null>;
  [key: string]: unknown;
}

/** Legacy name kept for the earlier WIP alias; the payload has no envelope. */
export type StatusView = StatusPayload;

export type DoctorCheckStatus = 'OK' | 'WARN' | 'FAIL' | 'SKIP' | 'INFO' | 'UNREACHABLE';

export interface DoctorCheck {
  check: string;
  status: DoctorCheckStatus | string;
  message: string;
}

/** GET /api/doctor returns a bare JSON array of DoctorCheck. */
export type DoctorView = DoctorCheck[];

/* ---- Readiness ---- */

export interface Readiness {
  status: 'ready' | 'degraded';
  database: 'available' | 'unreachable';
  reason_code: string;
}
