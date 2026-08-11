package com.agentstate.guard.ui

import com.agentstate.guard.ui.link.DeviceLinkUnsupportedException
import com.agentstate.guard.ui.link.pairingFailureReasonCode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

/**
 * Regression guard: raw exception text must never reach a UI-visible
 * reasonCode. Arbitrary failures collapse to a stable whitelisted code.
 */
class ReasonCodeMappingTest {

    private val sentinel = "DO_NOT_LEAK_RAW_EXCEPTION_TEXT"

    @Test
    fun `unsupported transport maps to its stable code`() {
        assertEquals(
            "DEVICE_LINK_UNSUPPORTED",
            pairingFailureReasonCode(DeviceLinkUnsupportedException())
        )
    }

    @Test
    fun `unknown exception maps to generic code without raw text`() {
        val code = pairingFailureReasonCode(
            IllegalStateException("$sentinel /secret/path?token=abc")
        )
        assertEquals("PAIRING_FAILED", code)
        assertFalse(code.contains(sentinel))
    }

    @Test
    fun `null-message exception maps to generic code`() {
        assertEquals("PAIRING_FAILED", pairingFailureReasonCode(RuntimeException()))
    }

    @Test
    fun `production ui sources never read Exception message into state`() {
        // Source-level guard: no `.message` read may survive in the UI layer;
        // reason codes come only from the whitelisted mapper.
        val offenders = mutableListOf<String>()
        File("src/main/java/com/agentstate/guard/ui").walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .filter { !it.invariantSeparatorsPath.contains("/preview/") }
            .forEach { file ->
                file.readLines().forEachIndexed { index, line ->
                    if (line.contains("Exception") && line.contains(".message")) {
                        offenders += "${file.path}:${index + 1}"
                    }
                }
            }
        assertEquals("raw exception text reachable from UI: $offenders", emptyList<String>(), offenders)
    }
}
