/**
 * R4-P8 authoritative read DTO types.
 *
 * Mirrors the frozen backend contract in docs/phases/08-ui-backend-contract.md
 * (schema_version "r4-p8-1"). The UI must render only what the API returns and
 * must never upgrade, infer, or fabricate authoritative state.
 */

export const R4_SCHEMA_VERSION = 'r4-p8-1' as const;

export type ViewName = 'runtime' | 'agents' | 'supervision' | 'recovery';

/** Top-level projection status. UNKNOWN/DEGRADED are never "fine". */
export type ViewStatus = 'EMPTY' | 'UNKNOWN' | 'DEGRADED' | 'AVAILABLE';

export interface R4ViewDto<TItem> {
  schema_version: string;
  view: ViewName;
  status: ViewStatus;
  reason_code: string;
  evidence_refs: string[];
  items: TItem[];
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
  role: string;
  /** Backend-owned lifecycle (e.g. DETECTED | UNKNOWN). Rendered verbatim. */
  lifecycle: string;
  confidence: number;
  execution_domain_id: string | null;
  workspace: AgentWorkspace;
  reason_code: string;
  uncertainty: boolean;
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
  status: string;
  policy_decision: string | null;
  manual_approval: boolean;
  requires_manual_approval: boolean;
  requires_checkpoint: boolean;
  ai_assessment: SupervisionAiAssessment | null;
  recovery_facts: SupervisionRecoveryFacts | null;
  /**
   * Opaque server-issued one-time action binding (64 lowercase hex), present
   * only while a REVIEW session genuinely awaits manual approval. Never
   * generated, stored, or reused across sessions by the client.
   */
  action_ref: string | null;
  evidence_refs: string[];
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

export type SupervisionView = R4ViewDto<SupervisionItem>;

/* ---- Recovery ---- */

export type RecoveryLevel = 'R0' | 'R1' | 'R2' | 'R3';

/** Trusted Baseline is a separate trust state, never implied by recovery level. */
export type TrustedBaselineStatus = 'NONE' | 'TRUSTED' | 'RETIRED' | 'REVOKED';

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
}

export type RecoveryView = R4ViewDto<RecoveryItem> & RecoverySummaryFields;

/* ---- Readiness ---- */

export interface Readiness {
  status: 'ready' | 'degraded';
  database: 'available' | 'unreachable';
  reason_code: string;
}
