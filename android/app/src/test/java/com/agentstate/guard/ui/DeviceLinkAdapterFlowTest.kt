package com.agentstate.guard.ui

import com.agentstate.guard.network.BindingStore
import com.agentstate.guard.network.BoundDesktop
import com.agentstate.guard.network.DeviceClientFactory
import com.agentstate.guard.network.DeviceEndpoint
import com.agentstate.guard.network.DeviceEndpointRediscovery
import com.agentstate.guard.network.DeviceHttpTransport
import com.agentstate.guard.network.DeviceLinkClient
import com.agentstate.guard.network.DeviceLinkHttpException
import com.agentstate.guard.network.DeviceLinkRepository
import com.agentstate.guard.network.DeviceSigner
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.RepositoryDeviceLinkUiAdapter
import com.agentstate.guard.ui.screens.canSubmitSupervision
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.SupervisionSessionUi
import com.agentstate.guard.ui.state.SupervisionUiState
import java.io.IOException
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Real adapter/repository/client composition; only HTTP transport and KeyStore are test doubles. */
class DeviceLinkAdapterFlowTest {
    private class Store : BindingStore {
        var value: BoundDesktop? = null
        override fun load() = value
        override fun save(binding: BoundDesktop) { value = binding }
        override fun clear() { value = null }
    }
    private class Signer : DeviceSigner {
        var deleted = false
        var verify = true
        override fun publicKeyDer() = byteArrayOf(4)
        override fun sign(message: ByteArray) = byteArrayOf(0x30)
        override fun verifyDesktop(publicKeyDer: ByteArray, message: ByteArray, signature: ByteArray) = verify
        override fun delete() { deleted = true }
    }
    private data class Call(val path: String, val body: JSONObject?)
    private class Transport : DeviceHttpTransport {
        val calls = mutableListOf<Call>()
        var bothConfirmed = false
        var sas = "123 456"
        var bound = false
        var loseCompletion = false
        var failCompletion = false
        var malformedAction = false
        override fun request(endpoint: DeviceEndpoint, path: String, method: String, body: JSONObject?, headers: Map<String, String>): JSONObject {
            calls += Call(path, body)
            return when {
                path.endsWith("/connect") -> JSONObject().put("state", "first_connection").put("pairing_token", "p".repeat(64))
                path.endsWith("/sas") -> JSONObject().put("state", "sas_pending").put("sas", sas)
                path.endsWith("/confirm") -> JSONObject().put("state",
                    if (body!!.getBoolean("confirm")) { if (bothConfirmed) "confirmed_both" else "sas_pending" } else "rejected")
                path.endsWith("/complete") -> {
                    if (failCompletion) throw IOException("not committed")
                    bound = true
                    if (loseCompletion) throw IOException("reply lost")
                    JSONObject().put("status", "bound").put("session_token", "s".repeat(64))
                }
                path.endsWith("/auth/challenge") -> {
                    if (!bound) throw DeviceLinkHttpException(403, "DEVICE_NOT_BOUND")
                    JSONObject().put("challenge_id", "1".repeat(32)).put("desktop_challenge", "2".repeat(64))
                }
                path.endsWith("/auth/response") -> JSONObject().put("session_token", "s".repeat(64)).put("desktop_signature", "30")
                path.contains("/evidence/") -> JSONObject().put("event_id", path.substringAfterLast('/'))
                    .put("status", "AVAILABLE").put("event_type", "USER_APPROVED").put("result", "APPROVED")
                path.endsWith("/approve-once") || path.endsWith("/reject") -> if (malformedAction) JSONObject() else
                    JSONObject().put("schema_version", "r4-p8-action-1")
                        .put("supervision_session_id", path.split('/').dropLast(1).last())
                        .put("action", if (path.endsWith("/approve-once")) "APPROVE_ONCE" else "REJECT")
                        .put("consumed", true).put("status", if (path.endsWith("/approve-once")) "APPROVED" else "REJECTED")
                else -> JSONObject().put("status", "AVAILABLE").put("view", path.substringAfterLast('/'))
            }
        }
    }
    private class Fixture {
        val store = Store()
        val signer = Signer()
        val transport = Transport()
        val repository = DeviceLinkRepository(store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) })
        val adapter = RepositoryDeviceLinkUiAdapter(repository, store, Dispatchers.Unconfined)
        private val publicKey = "04"
        private val fingerprint = MessageDigest.getInstance("SHA-256").digest(byteArrayOf(4))
            .joinToString("") { "%02x".format(it.toInt() and 255) }
        val invitation = "agentstate://pair?v=1&host=192.168.1.9&port=8788&uuid=desktop-a&pub=$publicKey&sign_fp=$fingerprint&tls_fp=${"a".repeat(64)}&sid=${"1".repeat(32)}&ticket=${"2".repeat(64)}&exp=${System.currentTimeMillis() / 1000 + 120}"
        suspend fun pair(): String {
            val state = adapter.beginScanPairing(invitation)
            transport.bothConfirmed = true
            assertEquals(PairingPhase.PAIRED, adapter.confirmSas(state.pairingId!!).phase)
            return state.pairingId!!
        }
    }
    @Test fun pairingRequiresExplicitConfirmationAndCompletesAndroidFirstOrder() = runBlocking {
        val f = Fixture()
        val pending = f.adapter.beginScanPairing(f.invitation)
        assertEquals(PairingPhase.SAS_PENDING, pending.phase)
        assertEquals("123 456", pending.sasCode)
        assertNull(f.store.value)
        f.adapter.pollPairing(pending.pairingId!!)
        assertFalse(f.transport.calls.any { it.path.endsWith("/confirm") })
        assertEquals(PairingPhase.CONFIRMING, f.adapter.confirmSas(pending.pairingId!!).phase)
        assertNull(f.store.value)
        f.transport.bothConfirmed = true
        assertEquals(PairingPhase.PAIRED, f.adapter.pollPairing(pending.pairingId!!).phase)
        assertEquals("desktop-a", f.store.value?.desktopUuid)
        assertEquals(DataPhase.CONNECTED, f.adapter.homeState().phase)
        assertEquals(1, f.transport.calls.count { it.path.endsWith("/complete") })
    }
    @Test fun rejectionAndMalformedCodeNeverCreateLocalBinding() = runBlocking {
        val f = Fixture()
        val pending = f.adapter.beginScanPairing(f.invitation)
        assertEquals(PairingPhase.REJECTED, f.adapter.rejectSas(pending.pairingId!!).phase)
        assertNull(f.store.value)
        f.transport.sas = "invented"
        assertEquals(PairingPhase.ERROR, f.adapter.beginScanPairing(f.invitation).phase)
        assertNull(f.store.value)
    }
    @Test fun lostCompletionReplyUsesMutualAuthInsteadOfReplayingCompletion() = runBlocking {
        val f = Fixture(); f.transport.loseCompletion = true
        f.pair()
        assertNotNull(f.store.value)
        assertEquals(1, f.transport.calls.count { it.path.endsWith("/complete") })
        assertEquals(1, f.transport.calls.count { it.path.endsWith("/auth/challenge") })
        assertEquals(1, f.transport.calls.count { it.path.endsWith("/auth/response") })
    }
    @Test fun failedOrUnverifiedCompletionNeverPersistsTrust() = runBlocking {
        val f = Fixture(); f.transport.failCompletion = true
        val pending = f.adapter.beginScanPairing(f.invitation); f.transport.bothConfirmed = true
        assertEquals(PairingPhase.ERROR, f.adapter.confirmSas(pending.pairingId!!).phase)
        assertNull(f.store.value)
        val second = Fixture(); second.transport.loseCompletion = true; second.signer.verify = false
        val next = second.adapter.beginScanPairing(second.invitation); second.transport.bothConfirmed = true
        assertEquals(PairingPhase.ERROR, second.adapter.confirmSas(next.pairingId!!).phase)
        assertNull(second.store.value)
    }
    @Test fun evidenceReadsExactEventAndActionsSendOnlyServerIntent() = runBlocking {
        val f = Fixture(); f.pair()
        val evidence = f.adapter.evidenceState("event-a")
        assertEquals("event-a", evidence.eventId); assertEquals("USER_APPROVED", evidence.eventType)
        assertEquals(DataPhase.ERROR, f.adapter.evidenceState("../private").phase)
        val ref = "a".repeat(64)
        assertTrue(f.adapter.approveOnce("session-a", ref).succeeded)
        assertTrue(f.adapter.rejectSupervision("session-a", ref).succeeded)
        val actions = f.transport.calls.filter { it.path.endsWith("/approve-once") || it.path.endsWith("/reject") }
        assertEquals(2, actions.size)
        actions.forEach { val body = it.body!!; assertEquals(setOf("action_ref"), body.keys().asSequence().toSet()); assertEquals(ref, body.getString("action_ref")) }
        f.transport.malformedAction = true
        assertFalse(f.adapter.approveOnce("session-a", ref).succeeded)
        f.adapter.unpair()
        assertNull(f.store.value); assertTrue(f.signer.deleted)
    }
    @Test fun cachedMissingAndChangedSupervisionIntentCannotBeSubmitted() {
        val item = SupervisionSessionUi("s", "AWAITING_APPROVAL", "REVIEW", true, null,
            "a".repeat(64), true, null, null)
        val state = SupervisionUiState(DataPhase.CONNECTED, sessions = listOf(item))
        assertTrue(canSubmitSupervision(state, item))
        assertFalse(canSubmitSupervision(state.copy(lastKnown = true), item))
        assertFalse(canSubmitSupervision(state.copy(phase = DataPhase.UNREACHABLE), item))
        assertFalse(canSubmitSupervision(state, item.copy(actionRef = null)))
        assertFalse(canSubmitSupervision(state, item.copy(status = "APPROVED")))
    }
}
