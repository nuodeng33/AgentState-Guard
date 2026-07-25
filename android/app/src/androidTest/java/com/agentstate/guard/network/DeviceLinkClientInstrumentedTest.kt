package com.agentstate.guard.network

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.Assert.*

/**
 * Instrumented test that exercises DeviceLinkClient against the Python Gateway
 * running on the host. The emulator reaches the host via 10.0.2.2.
 *
 * Prerequisite: Python Gateway running on host at 10.0.2.2:8790
 *   python -m agentguard serve --port 8790
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class DeviceLinkClientInstrumentedTest {

    companion object {
        private const val GATEWAY_HOST = "10.0.2.2"
        private const val GATEWAY_PORT = 8790
    }

    @Test
    fun fullPairingFlow_viaHttp() {
        val client = DeviceLinkClient("http://$GATEWAY_HOST:$GATEWAY_PORT")

        // 1. Start pairing
        val start = client.pairStart()
        assertNotNull("session_id must not be null", start.sessionId)
        assertTrue("session_id must not be empty", start.sessionId.isNotEmpty())
        assertNotNull("desktop_uuid must not be null", start.desktopUuid)
        val sid = start.sessionId

        // 2. First connection
        val nonce = "ab" + "ab".repeat(15)  // 32-char hex = 16 bytes
        val connState = client.pairConnect(sid, "android-test-uuid", nonce)
        assertTrue("connect state should be first_connection or created",
            connState in setOf("first_connection", "created"))

        // 3. Send pubkey + get SAS
        val pubkeyDerHex = "04" + "a".repeat(128)  // dummy P-256 uncompressed key
        val sas = client.pairSas(sid, pubkeyDerHex)
        assertNotNull("SAS must not be null", sas)
        assertTrue("SAS should be 7 chars (XXX XXX)", sas.length == 7)
        assertTrue("SAS should contain space", sas.contains(" "))

        // 4. Confirm
        val confirmState = client.pairConfirm(sid, confirm = true)
        assertEquals("state should be confirmed_both", "confirmed_both", confirmState)

        // 5. Complete
        val boundStatus = client.pairComplete(sid, "android-test-uuid", pubkeyDerHex, "Emulator Test Device")
        assertEquals("status should be bound", "bound", boundStatus)

        // 6. Read status (authenticated via session token set by pairComplete)
        val status = client.getStatus()
        assertTrue("status should have bound_devices or desktop_uuid",
            status.has("bound_devices") || status.has("desktop_uuid"))
    }
}
