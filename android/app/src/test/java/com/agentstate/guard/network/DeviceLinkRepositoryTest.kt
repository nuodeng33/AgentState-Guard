package com.agentstate.guard.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class DeviceLinkRepositoryTest {
    private val first = DeviceEndpoint("192.168.1.9", 8788, "a".repeat(64))
    private val moved = DeviceEndpoint("192.168.1.10", 8788, "a".repeat(64))
    private val binding = BoundDesktop(first, "desktop-a", "04", "b".repeat(64), "android-a")

    @Test
    fun refreshPreservesLastKnownAsOfflineAndUnpairDeletesLocalTrust() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport(*successfulRefresh())
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        val online = repository.refresh()
        assertTrue(online.online)
        assertEquals("environment", online.environment?.getString("view"))

        transport.failure = IOException("offline")
        val offline = repository.refresh()
        assertFalse(offline.online)
        assertEquals("environment", offline.environment?.getString("view"))

        repository.unpairLocal()
        assertNull(store.load())
        assertTrue(signer.deleted)
    }

    @Test
    fun failedLastKnownEndpointRediscoveryReauthenticatesBeforeSavingNewEndpoint() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val failed = ScriptedTransport().apply { failure = IOException("moved") }
        val recovered = ScriptedTransport(*successfulRefresh())
        var created = 0
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { moved },
            DeviceClientFactory {
                created += 1
                DeviceLinkClient(it, if (created == 1) failed else recovered)
            },
        )

        val snapshot = repository.refresh()

        assertTrue(snapshot.online)
        assertEquals(moved.host, store.load()?.endpoint?.host)
        assertEquals(2, created)
    }

    @Test
    fun unauthenticatedRediscoveryCandidateDoesNotReplaceDurableEndpoint() {
        val store = MemoryBindingStore(binding)
        val failed = ScriptedTransport().apply { failure = IOException("unreachable") }
        val repository = DeviceLinkRepository(
            store, FakeSigner(), DeviceEndpointRediscovery { moved },
            DeviceClientFactory { DeviceLinkClient(it, failed) },
        )

        assertFalse(repository.refresh().online)
        assertEquals(first.host, store.load()?.endpoint?.host)
    }

    private fun successfulRefresh(): Array<JSONObject> = arrayOf(
        JSONObject().put("challenge_id", "1".repeat(32))
            .put("desktop_challenge", "2".repeat(64)),
        JSONObject().put("session_token", "3".repeat(64))
            .put("desktop_signature", "30"),
        JSONObject().put("view", "status"),
        JSONObject().put("view", "environment"),
        JSONObject().put("view", "agents"),
        JSONObject().put("view", "supervision"),
        JSONObject().put("view", "changes"),
        JSONObject().put("view", "recovery"),
        JSONObject().put("view", "advisory"),
    )
}

private class MemoryBindingStore(private var binding: BoundDesktop?) : BindingStore {
    override fun load(): BoundDesktop? = binding
    override fun save(binding: BoundDesktop) { this.binding = binding }
    override fun clear() { binding = null }
}

private class FakeSigner : DeviceSigner {
    var deleted = false
    override fun publicKeyDer(): ByteArray = byteArrayOf(4)
    override fun sign(message: ByteArray): ByteArray = byteArrayOf(0x30)
    override fun verifyDesktop(
        publicKeyDer: ByteArray, message: ByteArray, signature: ByteArray
    ): Boolean = true
    override fun delete() { deleted = true }
}

private class ScriptedTransport(vararg responses: JSONObject) : DeviceHttpTransport {
    private val responses = ArrayDeque(responses.toList())
    var failure: IOException? = null

    override fun request(
        endpoint: DeviceEndpoint,
        path: String,
        method: String,
        body: JSONObject?,
        headers: Map<String, String>,
    ): JSONObject {
        failure?.let { throw it }
        return responses.removeFirst()
    }
}
