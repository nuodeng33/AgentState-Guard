package com.agentstate.guard.qr

import com.agentstate.guard.network.QrPayload

/**
 * One-shot scanner controller between the camera loop and the pairing layer.
 *
 * A scanned text is routed through [QrPayload.parse] immediately: malformed
 * or expired payloads are dropped with the generic PAIR_QR_INVALID outcome —
 * they never reach the network or the pairing state machine. A successful
 * decode completes once with the raw payload text; the pairing layer owns
 * the parsed payload from there. The scanner never throws: unreadable
 * frames and engine failures both yield null (keep scanning).
 */
class QrScanner(
    private val engine: QrDecodeEngine,
    private val nowEpochSeconds: () -> Long = { System.currentTimeMillis() / 1000 },
) {
    sealed interface Outcome {
        /** A strictly valid, unexpired invitation; [payloadText] is the raw QR text. */
        data class Found(val payloadText: String) : Outcome

        /** The QR decoded but is not a valid AgentState invitation. */
        data class Invalid(val reasonCode: String) : Outcome
    }

    fun scanGrid(grid: QrPixelGrid): Outcome? = try {
        val text = engine.decode(QrFrame { grid }) ?: return null
        when (QrPayload.parse(text, nowEpochSeconds()) == null) {
            true -> Outcome.Invalid("PAIR_QR_INVALID")
            false -> Outcome.Found(text)
        }
    } catch (_: IllegalArgumentException) {
        null // A frame that fails the raw grid contract is silent scan noise.
    } catch (_: RuntimeException) {
        null // Engine/format failures stay silent; the camera loop keeps scanning.
    }
}
