package com.agentstate.guard.network

import org.json.JSONObject
import java.io.IOException
import java.net.URL
import java.security.MessageDigest
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

interface DeviceHttpTransport {
    fun request(
        endpoint: DeviceEndpoint,
        path: String,
        method: String,
        body: JSONObject?,
        headers: Map<String, String>,
    ): JSONObject
}

class DeviceLinkHttpException(val status: Int, val reasonCode: String) : IOException(reasonCode)

/** Real HTTPS transport pinned to the Desktop TLS public key. */
class PinnedHttpsTransport(private val timeoutMs: Int = 15_000) : DeviceHttpTransport {
    override fun request(
        endpoint: DeviceEndpoint,
        path: String,
        method: String,
        body: JSONObject?,
        headers: Map<String, String>,
    ): JSONObject {
        val trust = PinnedTrustManager(endpoint.tlsSpkiFingerprint)
        val context = SSLContext.getInstance("TLSv1.3")
        context.init(null, arrayOf(trust), null)
        val connection = URL(endpoint.baseUrl + path).openConnection() as HttpsURLConnection
        connection.sslSocketFactory = context.socketFactory
        connection.requestMethod = method
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("Content-Type", "application/json")
        headers.forEach(connection::setRequestProperty)
        if (body != null) {
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val status = connection.responseCode
        val text = (if (status in 200..299) connection.inputStream else connection.errorStream)
            ?.bufferedReader()?.use { it.readText() } ?: "{}"
        connection.disconnect()
        val json = JSONObject(text)
        if (status !in 200..299) {
            val code = json.optJSONObject("error")?.optString("code") ?: "DEVICE_HTTP_$status"
            throw DeviceLinkHttpException(status, code)
        }
        return json
    }
}

private class PinnedTrustManager(private val expectedFingerprint: String) : X509TrustManager {
    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw java.security.cert.CertificateException("missing certificate")
        val actual = MessageDigest.getInstance("SHA-256")
            .digest(leaf.publicKey.encoded).joinToString("") { "%02x".format(it.toInt() and 0xff) }
        if (!MessageDigest.isEqual(actual.toByteArray(), expectedFingerprint.toByteArray())) {
            throw java.security.cert.CertificateException("TLS pin mismatch")
        }
    }
}

/** Bounded Device Link transport. Tokens never leave process memory. */
class DeviceLinkClient(
    private val endpoint: DeviceEndpoint,
    private val transport: DeviceHttpTransport = PinnedHttpsTransport(),
) {
    private var pairingToken: String? = null
    private var sessionToken: String? = null

    fun pairConnect(payload: QrPayload, androidUuid: String, nonceHex: String): String {
        require(payload.endpoint == endpoint)
        val response = post(
            "/device/v1/pair/${payload.sessionId}/connect",
            JSONObject().put("ticket", payload.ticket)
                .put("android_uuid", androidUuid).put("nonce", nonceHex),
        )
        pairingToken = response.getString("pairing_token")
        return response.getString("state")
    }

    fun pairSas(sessionId: String, publicKeyDerHex: String): String = post(
        "/device/v1/pair/$sessionId/sas",
        JSONObject().put("android_pubkey_der_hex", publicKeyDerHex),
        pairing = true,
    ).getString("sas")

    fun pairConfirm(sessionId: String, confirm: Boolean): String = post(
        "/device/v1/pair/$sessionId/confirm", JSONObject().put("confirm", confirm),
        pairing = true,
    ).getString("state")

    fun pairComplete(
        sessionId: String,
        androidUuid: String,
        publicKeyDerHex: String,
        displayName: String,
    ): String {
        val response = post(
            "/device/v1/pair/$sessionId/complete",
            JSONObject().put("android_uuid", androidUuid)
                .put("android_pubkey_der_hex", publicKeyDerHex)
                .put("display_name", displayName).put("protocol_version", 1),
            pairing = true,
        )
        sessionToken = response.getString("session_token")
        pairingToken = null
        return response.getString("status")
    }

    fun authenticate(binding: BoundDesktop, signer: DeviceSigner) {
        val challenge = post(
            "/device/v1/auth/challenge",
            JSONObject().put("device_uuid", binding.androidUuid).put("protocol_version", 1),
        )
        val challengeId = challenge.getString("challenge_id")
        val challengeBytes = challenge.getString("desktop_challenge").hexBytes()
        val message = buildAuthMessage(
            binding.desktopUuid, binding.androidUuid, challengeId, challengeBytes
        )
        val response = post(
            "/device/v1/auth/response",
            JSONObject().put("device_uuid", binding.androidUuid)
                .put("challenge_id", challengeId)
                .put("signature", signer.sign(message).hex()).put("protocol_version", 1),
        )
        if (!signer.verifyDesktop(
                binding.desktopPublicKeyDerHex.hexBytes(),
                message,
                response.getString("desktop_signature").hexBytes(),
            )
        ) throw SecurityException("Desktop mutual authentication failed")
        sessionToken = response.getString("session_token")
    }

    fun clearSession() { sessionToken = null }
    fun getStatus(): JSONObject = get("/device/v1/status")
    fun getEnvironment(): JSONObject = get("/device/v1/environment")
    fun getAgents(): JSONObject = get("/device/v1/agents")
    fun getSupervision(): JSONObject = get("/device/v1/supervision")
    fun getChanges(): JSONObject = get("/device/v1/changes")
    fun getCheckpoints(): JSONObject = get("/device/v1/checkpoints")
    fun getRecovery(): JSONObject = get("/device/v1/recovery")
    fun getEvidence(eventId: String): JSONObject = get("/device/v1/evidence/$eventId")
    fun getAiAdvisory(): JSONObject = get("/device/v1/ai/advisory")
    fun approveOnce(sessionId: String, actionRef: String): JSONObject = post(
        "/device/v1/supervision/$sessionId/approve-once", JSONObject().put("action_ref", actionRef),
    )
    fun reject(sessionId: String, actionRef: String): JSONObject = post(
        "/device/v1/supervision/$sessionId/reject", JSONObject().put("action_ref", actionRef),
    )

    private fun get(path: String) = request(path, "GET", null)
    private fun post(path: String, body: JSONObject, pairing: Boolean = false) =
        request(path, "POST", body, pairing)

    private fun request(path: String, method: String, body: JSONObject?, pairing: Boolean = false): JSONObject {
        val headers = mutableMapOf<String, String>()
        if (pairing) pairingToken?.let { headers["X-Pairing-Token"] = it }
        sessionToken?.let { headers["Authorization"] = "Bearer $it" }
        return transport.request(endpoint, path, method, body, headers)
    }

    companion object {
        fun buildAuthMessage(
            desktopUuid: String,
            deviceUuid: String,
            challengeId: String,
            challenge: ByteArray,
        ): ByteArray {
            fun prefix(value: ByteArray) = java.nio.ByteBuffer.allocate(4 + value.size)
                .putInt(value.size).put(value).array()
            return listOf(
                "ASDL\u0000AUTH_RESPONSE\u0000".toByteArray(),
                java.nio.ByteBuffer.allocate(2).putShort(1.toShort()).array(),
                prefix(desktopUuid.toByteArray()), prefix(deviceUuid.toByteArray()),
                prefix(challengeId.hexBytes()), prefix(challenge),
            ).fold(ByteArray(0)) { acc, bytes -> acc + bytes }
        }
    }
}

internal fun String.hexBytes(): ByteArray {
    require(length % 2 == 0)
    return chunked(2).map { it.toInt(16).toByte() }.toByteArray()
}

internal fun ByteArray.hex(): String = joinToString("") { "%02x".format(it.toInt() and 0xff) }
