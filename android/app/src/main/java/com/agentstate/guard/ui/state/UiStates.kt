package com.agentstate.guard.ui.state

/**
 * UI-shell data phases for the Android product surfaces.
 *
 * These phases describe what the UI knows, never what authority it assumes.
 * UNREACHABLE / DEGRADED / EMPTY are never presented as healthy. When no
 * Device Link adapter is wired in, screens show EMPTY/UNREACHABLE-style
 * honest states ("no data yet / waiting for computer state") instead of
 * fabricated environment data.
 */
enum class DataPhase {
    LOADING,
    CONNECTED,
    DEGRADED,
    UNREACHABLE,
    EMPTY,
    UNKNOWN,
    ERROR,
}

/**
 * Freshness markers shared by every projection. [lastKnown] means the data
 * is cached, not live; [observedAt] is the verbatim backend source time;
 * [syncedAtEpochMs] is the local wall-clock instant of the last successful refresh, epoch ms.
 */
interface ProjectedState {
    val lastKnown: Boolean
    val observedAt: String?
    val syncedAtEpochMs: Long?
}

/** One runtime/tool row; status is a machine token rendered verbatim. */
data class EnvironmentItemUi(
    val name: String,
    val version: String?,
    val status: String,
    /** Verbatim per-item machine code when the backend supplies one. */
    val reasonCode: String? = null,
    val observedAt: String? = null,
)

data class EnvironmentUiState(
    val phase: DataPhase,
    val items: List<EnvironmentItemUi> = emptyList(),
    val reasonCode: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

data class CheckpointUi(
    val id: String,
    val label: String,
    val createdAt: String,
    /** Verbatim checkpoint-level facts when the backend supplies them. */
    val status: String? = null,
    val manifestIntegrity: String? = null,
)

data class CheckpointsUiState(
    val phase: DataPhase,
    val items: List<CheckpointUi> = emptyList(),
    val reasonCode: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

data class ChangeUi(
    val file: String,
    val changeType: String,
    val whenText: String?,
    /** Ledger event identifier, for evidence drill-down. */
    val eventId: String? = null,
    val actor: String? = null,
    val subject: String? = null,
    val result: String? = null,
    val reasonCode: String? = null,
    val checkpointId: String? = null,
    val affectedObjects: List<String>? = null,
    val verificationSummary: String? = null,
    val executionDomainId: String? = null,
    val attribution: String? = null,
    val changeKind: String? = null,
    val coverageBefore: String? = null,
    val coverageAfter: String? = null,
    val recoveryDisposition: String? = null,
    val workspaceId: String? = null,
    val evidenceRefs: List<String>? = null,
)

data class ChangesUiState(
    val phase: DataPhase,
    val items: List<ChangeUi> = emptyList(),
    val reasonCode: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

/** One verified ledger activity; every token renders verbatim. */
data class VerifiedActivityUi(
    val eventId: String,
    val type: String,
    val result: String?,
    val reasonCode: String?,
    val timestamp: String?,
    val checkpointId: String? = null,
)

/** One agent fact row from the supervision projection, verbatim tokens. */
data class SupervisionAgentUi(
    val identity: String,
    val role: String?,
    val lifecycle: String?,
    val observedAt: String? = null,
)

/**
 * One supervision session projection. [actionRef] is the opaque server-issued
 * approval intent handle; it is the only accepted mutation parameter and is
 * never interpreted by the UI.
 */
data class SupervisionSessionUi(
    val sessionId: String,
    val status: String,
    val policyDecision: String?,
    val pendingApproval: Boolean?,
    val blockedOrFailedReason: String?,
    val actionRef: String?,
    val requiresCheckpoint: Boolean?,
    val latestVerifiedActivity: VerifiedActivityUi?,
    val currentTask: String? = null,
    val currentPhase: String? = null,
    val currentAction: String? = null,
    val observedAt: String?,
)

data class SupervisionUiState(
    val phase: DataPhase,
    val pendingCount: Int? = null,
    val reasonCode: String? = null,
    val sessions: List<SupervisionSessionUi> = emptyList(),
    val observedAgents: List<SupervisionAgentUi> = emptyList(),
    val recentActivities: List<VerifiedActivityUi> = emptyList(),
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

/** Result of the only two Android mutations: approve-once / reject. */
data class SupervisionActionUiResult(
    val succeeded: Boolean,
    val status: String?,
    val reasonCode: String?,
)

data class RecoveryUiState(
    val phase: DataPhase,
    val recoveryLevel: String? = null,
    val reasonCode: String? = null,
    /** Verbatim backend token; VERIFIED only after real verified restore. */
    val actualRestoreStatus: String? = null,
    val testRestoreStatus: String? = null,
    val trustedBaselineStatus: String? = null,
    val checkpointCount: Int? = null,
    val verifiedAt: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

/** Advisory-only AI projection (contextual cards; never an authority, never
 * a secret). There is no standalone AI product surface on Android — only
 * AI-context inside Home / Environment / Supervision. [configured] means the
 * desktop has a live provider; the advisory is not authoritative regardless.
 */
data class AiAdvisoryUiState(
    val phase: DataPhase,
    val configured: Boolean? = null,
    val summary: String? = null,
    val severity: String? = null,
    val reasonCode: String? = null,
    val analyzedAt: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

/** Connected-home projection; every field is UI-safe and server-supplied. */
data class HomeUiState(
    val phase: DataPhase,
    val desktopName: String? = null,
    val overallStatus: String? = null,
    val runtimeSummary: String? = null,
    val agentsSummary: String? = null,
    val supervisionStatus: String? = null,
    val pendingSupervision: Int? = null,
    val changesStatus: String? = null,
    val changesCount: Int? = null,
    val lastCheckpoint: String? = null,
    val aiStatus: String? = null,
    val recoveryProjectionStatus: String? = null,
    val recoveryStatus: String? = null,
    val reasonCode: String? = null,
    override val lastKnown: Boolean = false,
    override val observedAt: String? = null,
    override val syncedAtEpochMs: Long? = null,
) : ProjectedState

/** A linked desktop as a UI-safe projection. */
data class LinkedDesktop(
    val id: String,
    val displayName: String,
    /** Durable desktop UUID from the local binding, when paired. */
    val desktopUuid: String? = null,
    /** SHA-256 signing fingerprint from the local binding, when paired. */
    val signingFingerprint: String? = null,
    /** Bound endpoint as "host:port" from the local binding, when paired. */
    val endpoint: String? = null,
)

/**
 * Evidence drill-down projection for one ledger event. Everything shown
 * comes verbatim from the backend sanitized DTO — no raw payload, no raw
 * ledger, no frontend-side filtering or synthesis.
 */
data class EvidenceUiState(
    val phase: DataPhase,
    val status: String? = null,
    val reasonCode: String? = null,
    val eventId: String? = null,
    val eventType: String? = null,
    val observedAt: String? = null,
    val recordedAt: String? = null,
    /** Verbatim backend source identifier when the backend supplies one. */
    val source: String? = null,
    val subject: String? = null,
    val result: String? = null,
    /** Flat "key value" summary lines derived from sanitized detail only. */
    val verificationSummary: List<String>? = null,
    /** Flat "key value" lines for sanitized_detail.affected_objects. */
    val affectedObjects: List<String>? = null,
    val checkpointId: String? = null,
    val changeId: String? = null,
    val chainRef: String? = null,
    val executionDomainId: String? = null,
    val attribution: String? = null,
    val changeKind: String? = null,
    val coverageBefore: String? = null,
    val coverageAfter: String? = null,
    val recoveryDisposition: String? = null,
    val workspaceId: String? = null,
    /** Related ledger refs, display-only — never treated as lookup keys. */
    val relatedEvidenceRefs: List<String>? = null,
)

/** Connection / About projection for the bound-link state. */
data class ConnectionUiState(
    val phase: DataPhase,
    val paired: Boolean = false,
    /** True only when the last refresh reached the desktop. */
    val online: Boolean = false,
    /** True when the displayed data is cached, not live. */
    val lastKnown: Boolean = false,
    /** Session/binding failed authentication; re-auth is required. */
    val authRequired: Boolean = false,
    val reasonCode: String? = null,
    /** Bound desktop facts from the durable local binding. */
    val desktopUuid: String? = null,
    val signingFingerprint: String? = null,
    val endpoint: String? = null,
    /** Verbatim backend source time of the latest snapshot. */
    val observedAt: String? = null,
    /** Local wall-clock instant of the last successful refresh, epoch ms. */
    val syncedAtEpochMs: Long? = null,
) {
    companion object {
        /** Honest default: nothing paired, nothing known. */
        val EMPTY = ConnectionUiState(phase = DataPhase.EMPTY, paired = false)
    }
}
