package com.agentstate.guard.network

import org.json.JSONObject
import java.io.IOException

fun interface DeviceEndpointRediscovery {
    fun rediscover(binding: BoundDesktop): DeviceEndpoint?
}

fun interface DeviceClientFactory {
    fun create(endpoint: DeviceEndpoint): DeviceLinkClient
}

data class DeviceLinkSnapshot(
    val online: Boolean,
    val status: JSONObject?,
    val environment: JSONObject?,
    val agents: JSONObject?,
    val supervision: JSONObject?,
    val changes: JSONObject?,
    val checkpoints: JSONObject?,
    val recovery: JSONObject?,
    val aiAdvisory: JSONObject?,
    val reasonCode: String?,
    /** True when the failure reason means the saved session/binding failed auth. */
    val authRequired: Boolean = false,
)

/** Foreground/explicit-refresh repository; no second authority or event bus. */
class DeviceLinkRepository(
    private val bindings: BindingStore,
    private val signer: DeviceSigner,
    private val rediscovery: DeviceEndpointRediscovery,
    private val clients: DeviceClientFactory = DeviceClientFactory { DeviceLinkClient(it) },
) {
    private var client: DeviceLinkClient? = null
    private var authenticated = false
    private var lastKnown: DeviceLinkSnapshot? = null

    fun beginPairing(payload: QrPayload, androidUuid: String): String {
        client = clients.create(payload.endpoint)
        authenticated = false
        return client!!.pairConnect(payload, androidUuid, randomNonce())
    }

    fun startSas(payload: QrPayload): String = clientOrThrow().pairSas(
        payload.sessionId, signer.publicKeyDer().hex()
    )

    fun confirmSas(payload: QrPayload, confirm: Boolean): String =
        clientOrThrow().pairConfirm(payload.sessionId, confirm)

    fun completePairing(payload: QrPayload, androidUuid: String, displayName: String): String {
        val status = clientOrThrow().pairComplete(
            payload.sessionId, androidUuid, signer.publicKeyDer().hex(), displayName
        )
        val binding = BoundDesktop(
            payload.endpoint, payload.desktopUuid, payload.desktopPublicKeyDerHex,
            payload.desktopSigningFingerprint, androidUuid,
        )
        bindings.save(binding)
        authenticated = true
        return status
    }

    fun refresh(): DeviceLinkSnapshot {
        val binding = bindings.load() ?: return offline("DEVICE_NOT_PAIRED")
        val active = client ?: clients.create(binding.endpoint).also { client = it }
        return try {
            ensureAuthenticated(active, binding)
            fetch(active)
        } catch (error: DeviceLinkHttpException) {
            if (error.status == 401) {
                active.clearSession()
                authenticated = false
                try {
                    ensureAuthenticated(active, binding)
                    fetch(active)
                } catch (second: DeviceLinkHttpException) {
                    reconnect(binding, second.reasonCode, authRejected(second))
                } catch (_: IOException) {
                    reconnect(binding, error.reasonCode)
                }
            } else offline(error.reasonCode, authRequired = authRejected(error))
        } catch (_: IOException) {
            reconnect(binding, "DEVICE_LINK_OFFLINE")
        }
    }

    fun approveOnce(sessionId: String, actionRef: String): JSONObject {
        val result = withAuthenticated { it.approveOnce(sessionId, actionRef) }
        lastKnown = null
        return result
    }

    fun reject(sessionId: String, actionRef: String): JSONObject {
        val result = withAuthenticated { it.reject(sessionId, actionRef) }
        lastKnown = null
        return result
    }

    /** Bounded read of one sanitized evidence event; read-only, no refresh. */
    fun evidence(eventId: String): JSONObject = withAuthenticated { it.getEvidence(eventId) }

    fun unpairLocal() {
        client?.clearSession()
        client = null
        authenticated = false
        lastKnown = null
        bindings.clear()
        signer.delete()
    }

    private fun reconnect(
        binding: BoundDesktop,
        reason: String,
        authRequired: Boolean = false,
    ): DeviceLinkSnapshot {
        val endpoint = rediscovery.rediscover(binding) ?: return offline(reason, authRequired)
        if (endpoint.tlsSpkiFingerprint != binding.endpoint.tlsSpkiFingerprint) {
            return offline("DEVICE_REDISCOVERY_IDENTITY_MISMATCH", authRequired = true)
        }
        val updated = binding.copy(endpoint = endpoint)
        val replacement = clients.create(endpoint)
        client = replacement
        authenticated = false
        return try {
            ensureAuthenticated(replacement, updated)
            bindings.save(updated)
            fetch(replacement)
        } catch (error: DeviceLinkHttpException) {
            offline(error.reasonCode, authRequired = authRejected(error))
        } catch (_: IOException) {
            offline(reason)
        }
    }

    private fun fetch(active: DeviceLinkClient): DeviceLinkSnapshot {
        val snapshot = DeviceLinkSnapshot(
            true, active.getStatus(), active.getEnvironment(), active.getAgents(),
            active.getSupervision(), active.getChanges(), active.getCheckpoints(),
            active.getRecovery(), active.getAiAdvisory(), null,
        )
        lastKnown = snapshot
        return snapshot
    }

    private fun ensureAuthenticated(active: DeviceLinkClient, binding: BoundDesktop) {
        if (!authenticated) {
            active.authenticate(binding, signer)
            authenticated = true
        }
    }

    private fun <T> withAuthenticated(action: (DeviceLinkClient) -> T): T {
        val binding = bindings.load() ?: throw IllegalStateException("DEVICE_NOT_PAIRED")
        val active = client ?: clients.create(binding.endpoint).also { client = it }
        ensureAuthenticated(active, binding)
        return try {
            action(active)
        } catch (error: DeviceLinkHttpException) {
            if (error.status != 401) throw error
            active.clearSession()
            authenticated = false
            ensureAuthenticated(active, binding)
            action(active)
        }
    }

    private fun offline(reason: String, authRequired: Boolean = false): DeviceLinkSnapshot {
        val previous = lastKnown
        return if (previous == null) {
            DeviceLinkSnapshot(
                false, null, null, null, null, null, null, null, null,
                reason, authRequired,
            )
        } else previous.copy(online = false, reasonCode = reason, authRequired = authRequired)
    }

    private fun authRejected(error: DeviceLinkHttpException): Boolean =
        error.status == 401 || error.status == 403

    private fun clientOrThrow() = client ?: throw IllegalStateException("PAIRING_NOT_STARTED")
    private fun randomNonce() = ByteArray(32).also {
        java.security.SecureRandom().nextBytes(it)
    }.hex()
}
