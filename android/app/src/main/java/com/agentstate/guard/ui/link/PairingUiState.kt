package com.agentstate.guard.ui.link

/**
 * Pairing UI state machine (presentation layer only).
 *
 * Mirrors the desktop Devices shell. No protocol fields, keys, or network
 * payloads live here; every value is a UI-safe projection supplied by a
 * DeviceLinkUiAdapter. SAS codes and pairing handles always come from the
 * adapter and are never generated in the UI.
 */
enum class PairingPhase {
    IDLE,
    PAIRING_CREATED,
    WAITING_FOR_DESKTOP,
    SAS_PENDING,
    CONFIRMING,
    PAIRED,
    EXPIRED,
    REJECTED,
    ERROR,
}

data class PairingUiState(
    val phase: PairingPhase,
    val pairingId: String? = null,
    val desktopName: String? = null,
    /** Six-digit SAS from the adapter; never generated client-side. */
    val sasCode: String? = null,
    /** Epoch milliseconds when the pairing offer expires. */
    val expiresAtEpochMs: Long? = null,
    /** Stable machine reason code for terminal states; rendered verbatim. */
    val reasonCode: String? = null,
)
