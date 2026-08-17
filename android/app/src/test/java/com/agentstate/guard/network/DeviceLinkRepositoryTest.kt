package com.agentstate.guard.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.SocketTimeoutException

class DeviceLinkRepositoryTest {
    private val first = DeviceEndpoint("192.168.1.9", 8788, "a".repeat(64))
    private val moved = DeviceEndpoint("192.168.1.10", 8788, "a".repeat(64))
    private val binding = BoundDesktop(first, "desktop-a", "04", "b".repeat(64), "android-a")

    @Test
    fun refreshPreservesLastKnownAsOffline() {
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


    @Test
    fun authenticatedSelfUnpairRevokesDesktopBeforeDeletingLocalTrust() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport(*successfulAuthentication(), selfUnpairResponse())
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        repository.unpair()

        assertEquals("/device/v1/self-unpair", transport.requests.last().path)
        assertEquals("POST", transport.requests.last().method)
        assertEquals(0, transport.requests.last().body?.length())
        assertEquals("Bearer ${"3".repeat(64)}", transport.requests.last().headers["Authorization"])
        assertNull(store.load())
        assertTrue(signer.deleted)
    }

    @Test
    fun failedRemoteSelfUnpairRetainsBindingAndSignerForRetry() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport(*successfulAuthentication()).apply {
            failurePath = "/device/v1/self-unpair"
            failure = DeviceLinkHttpException(503, "DEVICE_SELF_UNPAIR_UNAVAILABLE")
        }
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        val failure = try {
            repository.unpair()
            null
        } catch (error: DeviceLinkHttpException) {
            error
        }

        assertEquals("DEVICE_SELF_UNPAIR_UNAVAILABLE", failure?.reasonCode)
        assertEquals(binding, store.load())
        assertFalse(signer.deleted)
    }

    @Test
    fun desktopAlreadyRevokedProofAllowsTerminalLocalCleanup() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport().apply {
            failurePath = "/device/v1/auth/challenge"
            failure = DeviceLinkHttpException(403, "DEVICE_NOT_BOUND")
        }
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        repository.unpair()

        assertNull(store.load())
        assertTrue(signer.deleted)
    }

    @Test
    fun serverFailureCannotMasqueradeAsAlreadyRevokedTerminalProof() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport().apply {
            failurePath = "/device/v1/auth/challenge"
            failure = DeviceLinkHttpException(503, "DEVICE_NOT_BOUND")
        }
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        val failure = runCatching { repository.unpair() }.exceptionOrNull()

        assertTrue(failure is DeviceLinkHttpException)
        assertEquals(503, (failure as DeviceLinkHttpException).status)
        assertEquals(binding, store.load())
        assertFalse(signer.deleted)
    }

    @Test
    fun offlineAndTimeoutSelfUnpairFailuresRetainLocalTrust() {
        listOf(IOException("offline"), SocketTimeoutException("timeout")).forEach { transportFailure ->
            val store = MemoryBindingStore(binding)
            val signer = FakeSigner()
            val transport = ScriptedTransport(*successfulAuthentication()).apply {
                failurePath = "/device/v1/self-unpair"
                failure = transportFailure
            }
            val repository = DeviceLinkRepository(
                store, signer, DeviceEndpointRediscovery { null },
                DeviceClientFactory { DeviceLinkClient(it, transport) },
            )

            val thrown = runCatching { repository.unpair() }.exceptionOrNull()

            assertEquals(transportFailure, thrown)
            assertEquals(binding, store.load())
            assertFalse(signer.deleted)
        }
    }

    @Test
    fun ambiguousSuccessfulResponseRetainsLocalTrust() {
        val store = MemoryBindingStore(binding)
        val signer = FakeSigner()
        val transport = ScriptedTransport(
            *successfulAuthentication(),
            JSONObject().put("status", "MAYBE"),
        )
        val repository = DeviceLinkRepository(
            store, signer, DeviceEndpointRediscovery { null },
            DeviceClientFactory { DeviceLinkClient(it, transport) },
        )

        val failure = runCatching { repository.unpair() }.exceptionOrNull()

        assertTrue(failure is DeviceLinkResponseException)
        assertEquals(
            "DEVICE_SELF_UNPAIR_RESPONSE_INVALID",
            (failure as DeviceLinkResponseException).reasonCode,
        )
        assertEquals(binding, store.load())
        assertFalse(signer.deleted)
    }

    private fun successfulAuthentication(): Array<JSONObject> = arrayOf(
        JSONObject().put("challenge_id", "1".repeat(32))
            .put("desktop_challenge", "2".repeat(64)),
        JSONObject().put("session_token", "3".repeat(64))
            .put("desktop_signature", "30"),
    )

    private fun selfUnpairResponse(): JSONObject = JSONObject()
        .put("schema_version", "device-link-self-unpair-1")
        .put("action", "SELF_UNPAIR")
        .put("status", "UNPAIRED")
        .put("reason_code", "DEVICE_SELF_UNPAIRED")

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
        JSONObject().put("view", "checkpoints"),
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
    var failurePath: String? = null
    val requests = mutableListOf<RecordedRequest>()

    override fun request(
        endpoint: DeviceEndpoint,
        path: String,
        method: String,
        body: JSONObject?,
        headers: Map<String, String>,
    ): JSONObject {
        requests += RecordedRequest(endpoint, path, method, body, headers)
        failure?.takeIf { failurePath == null || failurePath == path }?.let { throw it }
        return responses.removeFirst()
    }
}
