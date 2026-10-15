package com.agentstate.guard.ui.link

import com.agentstate.guard.ui.state.AiMonitorUiState
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.state.ConnectionUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.state.RecoveryUiState
import com.agentstate.guard.ui.state.SupervisionActionUiResult
import com.agentstate.guard.ui.state.SupervisionUiState

/** Thrown when this build has no Device Link transport wired in. */
class DeviceLinkUnsupportedException : Exception("DEVICE_LINK_UNSUPPORTED")

/**
 * Maps a pairing failure to a small, stable, whitelisted machine reason code.
 *
 * Raw exception text is never UI-safe: it can carry implementation detail,
 * provider wording, paths, URLs, or other sensitive context. Unknown or
 * unclassified failures collapse to the generic PAIRING_FAILED code.
 */
fun pairingFailureReasonCode(error: Throwable): String = when (error) {
    is DeviceLinkUnsupportedException -> "DEVICE_LINK_UNSUPPORTED"
    else -> "PAIRING_FAILED"
}

/**
 * Maps a supervision mutation failure to a small, stable machine reason code.
 * Backend reason codes (SUPERVISION_*, DEVICE_* tokens) pass through verbatim;
 * unknown or unclassified failures collapse to SUPERVISION_ACTION_FAILED.
 */
fun supervisionFailureReasonCode(error: Throwable): String {
    val token = (error as? com.agentstate.guard.network.DeviceLinkHttpException)?.reasonCode
    return if (
        token != null &&
        (token.startsWith("SUPERVISION_") || token.startsWith("DEVICE_"))
    ) {
        token
    } else {
        "SUPERVISION_ACTION_FAILED"
    }
}

/**
 * Boundary between the Android UI and the Device Link integration.
 *
 * The UI only ever talks to this interface. Implementations return UI-safe
 * projections and never leak protocol internals, raw exceptions, or secrets.
 * The production implementation is [RepositoryDeviceLinkUiAdapter];
 * [NoopDeviceLinkUiAdapter] is the honest empty default used only in
 * previews and tests.
 */
interface DeviceLinkUiAdapter {
    /** The linked desktop, or null when unpaired. */
    suspend fun linkedDesktop(): LinkedDesktop?

    suspend fun homeState(): HomeUiState
    suspend fun environmentState(): EnvironmentUiState
    suspend fun changesState(): ChangesUiState
    suspend fun supervisionState(): SupervisionUiState
    suspend fun checkpointsState(): CheckpointsUiState
    suspend fun recoveryState(): RecoveryUiState
    suspend fun aiMonitorState(): AiMonitorUiState

    /** Connection / About projection (bound desktop facts, freshness, auth). */
    suspend fun connectionState(): ConnectionUiState

    /** Read-only evidence drill-down; default shells show NO_EVIDENCE. */
    suspend fun evidenceState(eventId: String): EvidenceUiState

    /** Approve one pending supervision intent exactly once. */
    suspend fun approveOnce(sessionId: String, actionRef: String): SupervisionActionUiResult

    /** Reject one pending supervision intent exactly once. */
    suspend fun rejectSupervision(sessionId: String, actionRef: String): SupervisionActionUiResult

    /** Remove the local binding and KeyStore identity; the next link needs a new QR+SAS. */
    suspend fun unpair()

    /** Bring projections up to date (app start, resume, explicit pull). Single-flight. */
    suspend fun refresh()

    /** Begin pairing from a scanned payload string (opaque to the UI). */
    suspend fun beginScanPairing(payload: String): PairingUiState

    /** Poll the current pairing state (progress, SAS availability, expiry). */
    suspend fun pollPairing(pairingId: String): PairingUiState

    /** User confirmed the SAS matches on both devices. */
    suspend fun confirmSas(pairingId: String): PairingUiState

    /** User reported the SAS does not match. */
    suspend fun rejectSas(pairingId: String): PairingUiState

    /** Abandon an in-flight pairing attempt. */
    suspend fun cancelPairing(pairingId: String)
}

/**
 * Default adapter for previews and tests: unpaired, every projection EMPTY,
 * pairing unsupported. Performs no I/O of any kind. Never the production
 * default — production binds [RepositoryDeviceLinkUiAdapter].
 */
class NoopDeviceLinkUiAdapter : DeviceLinkUiAdapter {
    override suspend fun linkedDesktop(): LinkedDesktop? = null
    override suspend fun homeState(): HomeUiState = HomeUiState(phase = DataPhase.EMPTY)
    override suspend fun environmentState(): EnvironmentUiState =
        EnvironmentUiState(phase = DataPhase.EMPTY)
    override suspend fun changesState(): ChangesUiState = ChangesUiState(phase = DataPhase.EMPTY)
    override suspend fun supervisionState(): SupervisionUiState =
        SupervisionUiState(phase = DataPhase.EMPTY)
    override suspend fun checkpointsState(): CheckpointsUiState =
        CheckpointsUiState(phase = DataPhase.EMPTY)
    override suspend fun recoveryState(): RecoveryUiState = RecoveryUiState(phase = DataPhase.EMPTY)
    override suspend fun aiMonitorState(): AiMonitorUiState =
        AiMonitorUiState(phase = DataPhase.EMPTY, configured = false)
    override suspend fun connectionState(): ConnectionUiState = ConnectionUiState.EMPTY
    override suspend fun evidenceState(eventId: String): EvidenceUiState =
        EvidenceUiState(phase = DataPhase.EMPTY)
    override suspend fun approveOnce(
        sessionId: String,
        actionRef: String,
    ): SupervisionActionUiResult = throw DeviceLinkUnsupportedException()
    override suspend fun rejectSupervision(
        sessionId: String,
        actionRef: String,
    ): SupervisionActionUiResult = throw DeviceLinkUnsupportedException()
    override suspend fun unpair() = throw DeviceLinkUnsupportedException()
    override suspend fun refresh() = Unit

    override suspend fun beginScanPairing(payload: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun pollPairing(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun confirmSas(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun rejectSas(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun cancelPairing(pairingId: String) = Unit
}
