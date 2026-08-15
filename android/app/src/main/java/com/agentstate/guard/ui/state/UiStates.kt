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
    ERROR,
}

/** One runtime/tool row; status is a machine token rendered verbatim. */
data class EnvironmentItemUi(
    val name: String,
    val version: String?,
    val status: String,
)

data class EnvironmentUiState(
    val phase: DataPhase,
    val items: List<EnvironmentItemUi> = emptyList(),
    val reasonCode: String? = null,
)

data class CheckpointUi(
    val id: String,
    val label: String,
    val createdAt: String,
)

data class CheckpointsUiState(
    val phase: DataPhase,
    val items: List<CheckpointUi> = emptyList(),
    val reasonCode: String? = null,
)

data class ChangeUi(
    val file: String,
    val changeType: String,
    val whenText: String,
)

data class ChangesUiState(
    val phase: DataPhase,
    val items: List<ChangeUi> = emptyList(),
    val reasonCode: String? = null,
)

data class SupervisionUiState(
    val phase: DataPhase,
    val pendingCount: Int = 0,
    val reasonCode: String? = null,
)

data class RecoveryUiState(
    val phase: DataPhase,
    val recoveryLevel: String? = null,
    val reasonCode: String? = null,
)

data class AiMonitorUiState(
    val phase: DataPhase,
    val configured: Boolean = false,
    val summary: String? = null,
)

/** Connected-home projection; every field is UI-safe and server-supplied. */
data class HomeUiState(
    val phase: DataPhase,
    val desktopName: String? = null,
    val overallStatus: String? = null,
    val runtimeSummary: String? = null,
    val agentsSummary: String? = null,
    val pendingSupervision: Int? = null,
    val changesCount: Int? = null,
    val lastCheckpoint: String? = null,
    val aiStatus: String? = null,
    val recoveryStatus: String? = null,
)

/** A linked desktop as a UI-safe projection. */
data class LinkedDesktop(
    val id: String,
    val displayName: String,
)
