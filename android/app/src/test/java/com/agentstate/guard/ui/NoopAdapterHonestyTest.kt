package com.agentstate.guard.ui

import com.agentstate.guard.ui.link.DeviceLinkUnsupportedException
import com.agentstate.guard.ui.link.NoopDeviceLinkUiAdapter
import com.agentstate.guard.ui.state.DataPhase
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The default adapter must keep every surface honest: unpaired, EMPTY
 * projections, and pairing attempts rejected as unsupported — no I/O.
 */
class NoopAdapterHonestyTest {
    private val adapter = NoopDeviceLinkUiAdapter()

    @Test
    fun `noop adapter is unpaired and every projection is EMPTY`() = runBlocking {
        assertNull(adapter.linkedDesktop())
        assertEquals(DataPhase.EMPTY, adapter.homeState().phase)
        assertEquals(DataPhase.EMPTY, adapter.environmentState().phase)
        assertEquals(DataPhase.EMPTY, adapter.changesState().phase)
        assertEquals(DataPhase.EMPTY, adapter.supervisionState().phase)
        assertEquals(DataPhase.EMPTY, adapter.checkpointsState().phase)
        assertEquals(DataPhase.EMPTY, adapter.recoveryState().phase)
        assertEquals(DataPhase.EMPTY, adapter.aiMonitorState().phase)
        assertTrue(adapter.environmentState().items.isEmpty())
        assertTrue(adapter.changesState().items.isEmpty())
        assertTrue(adapter.checkpointsState().items.isEmpty())
    }

    @Test
    fun `noop adapter never performs pairing`() = runBlocking {
        val attempts: List<suspend () -> Any> = listOf(
            { adapter.beginScanPairing("agentstate://pair?x") },
            { adapter.pollPairing("p") },
            { adapter.confirmSas("p") },
            { adapter.rejectSas("p") },
        )
        attempts.forEach { attempt ->
            try {
                attempt()
                throw AssertionError("expected DeviceLinkUnsupportedException")
            } catch (expected: DeviceLinkUnsupportedException) {
                // expected
            }
        }
        Unit
    }
}
