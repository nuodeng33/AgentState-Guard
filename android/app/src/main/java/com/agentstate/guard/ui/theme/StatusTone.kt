package com.agentstate.guard.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Semantic status tones shared by badges/cards. UNKNOWN is deliberately a
 * distinct slate and never collapses into a safe tone. The textual label
 * remains the authoritative channel.
 */
enum class StatusTone(val foreground: Color, val background: Color, val border: Color) {
    OK(Success, SuccessBg, SuccessBorder),
    WARNING(Warning, WarningBg, WarningBorder),
    DANGER(Danger, DangerBg, DangerBorder),
    NEUTRAL(Neutral, NeutralBg, NeutralBorder),
    INFO(Info, InfoBg, InfoBorder),
    UNKNOWN(UnknownSlate, UnknownBg, UnknownBorder),
}

/** Conservative mapping for machine states; anything unmapped stays non-affirmative. */
fun toneForMachineState(state: String): StatusTone = when (state.uppercase()) {
    "OK", "PASS", "AVAILABLE", "CONNECTED", "COMPLETE", "APPROVED" -> StatusTone.OK
    "REVIEW", "AWAITING_APPROVAL", "DEGRADED", "INSUFFICIENT" -> StatusTone.WARNING
    "BLOCK", "FAIL", "ERROR", "UNREACHABLE", "REJECTED", "MISSING" -> StatusTone.DANGER
    "UNKNOWN", "NOT INSTALLED", "EMPTY" -> StatusTone.UNKNOWN
    else -> StatusTone.NEUTRAL
}
