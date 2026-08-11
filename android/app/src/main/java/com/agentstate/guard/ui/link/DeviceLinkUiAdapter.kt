package com.agentstate.guard.ui.link

import com.agentstate.guard.ui.state.AiMonitorUiState
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.state.RecoveryUiState
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
 * Boundary between the Android UI and the future Device Link integration.
 *
 * The UI only ever talks to this interface. Implementations return UI-safe
 * projections and never leak protocol internals, raw exceptions, or secrets.
 * The real adapter is provided by the Device Link worker; until then
 * [NoopDeviceLinkUiAdapter] keeps every screen in an honest no-data state.
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

    /** Begin pairing from a scanned payload string (opaque to the UI). */
    suspend fun beginScanPairing(payload: String): PairingUiState

    /** Begin LAN discovery; resolves to the first observable shell state. */
    suspend fun beginLanDiscovery(): PairingUiState

    /** Begin pairing to a manually entered desktop address. */
    suspend fun beginManualPairing(address: String): PairingUiState

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
 * Default adapter for builds without Device Link: unpaired, every projection
 * EMPTY, pairing unsupported. Performs no I/O of any kind.
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

    override suspend fun beginScanPairing(payload: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun beginLanDiscovery(): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun beginManualPairing(address: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun pollPairing(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun confirmSas(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun rejectSas(pairingId: String): PairingUiState =
        throw DeviceLinkUnsupportedException()
    override suspend fun cancelPairing(pairingId: String) = Unit
}
