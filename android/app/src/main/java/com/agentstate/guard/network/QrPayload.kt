package com.agentstate.guard.network

import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

/** Strict, one-time Device Link QR invitation. */
data class QrPayload(
    val protocolVersion: Int,
    val endpoint: DeviceEndpoint,
    val desktopUuid: String,
    val desktopPublicKeyDerHex: String,
    val desktopSigningFingerprint: String,
    val sessionId: String,
    val ticket: String,
    val expiryEpochSeconds: Long,
) {
    companion object {
        private val hex64 = Regex("^[0-9a-f]{64}$")
        private val session = Regex("^[0-9a-f]{32}$")
        private val identifier = Regex("^[A-Za-z0-9._:-]{1,128}$")

        fun parse(uri: String, nowEpochSeconds: Long = System.currentTimeMillis() / 1000): QrPayload? {
            if (!uri.startsWith("agentstate://pair?")) return null
            val params = uri.substringAfter('?').split('&').mapNotNull {
                val parts = it.split('=', limit = 2)
                if (parts.size != 2) null else parts[0] to URLDecoder.decode(
                    parts[1], StandardCharsets.UTF_8.name()
                )
            }.toMap()
            return try {
                val version = params["v"]?.toInt() ?: return null
                val host = params["host"] ?: return null
                val port = params["port"]?.toInt() ?: return null
                val uuid = params["uuid"] ?: return null
                val publicKey = params["pub"] ?: return null
                val signingFingerprint = params["sign_fp"] ?: return null
                val tlsFingerprint = params["tls_fp"] ?: return null
                val sessionId = params["sid"] ?: return null
                val ticket = params["ticket"] ?: return null
                val expiry = params["exp"]?.toLong() ?: return null
                val computedSigningFingerprint = MessageDigest.getInstance("SHA-256")
                    .digest(publicKey.chunked(2).map { it.toInt(16).toByte() }.toByteArray())
                    .joinToString("") { "%02x".format(it.toInt() and 0xff) }
                if (
                    version != 1 || port != 8788 || !isPrivateIpv4Literal(host) ||
                    !identifier.matches(uuid) || !session.matches(sessionId) ||
                    !hex64.matches(signingFingerprint) || !hex64.matches(tlsFingerprint) ||
                    !hex64.matches(ticket) || publicKey.length !in 2..1024 ||
                    publicKey.length % 2 != 0 || !publicKey.matches(Regex("^[0-9a-f]+$")) ||
                    signingFingerprint != computedSigningFingerprint ||
                    expiry <= nowEpochSeconds
                ) return null
                QrPayload(
                    version,
                    DeviceEndpoint(host, port, tlsFingerprint),
                    uuid,
                    publicKey,
                    signingFingerprint,
                    sessionId,
                    ticket,
                    expiry,
                )
            } catch (_: IllegalArgumentException) {
                null
            }
        }

    }
}

data class DeviceEndpoint(val host: String, val port: Int, val tlsSpkiFingerprint: String) {
    init {
        require(isPrivateIpv4Literal(host) && port == 8788)
        require(tlsSpkiFingerprint.matches(Regex("^[0-9a-f]{64}$")))
    }

    val baseUrl: String get() = "https://$host:$port"
}

private fun isPrivateIpv4Literal(host: String): Boolean {
    val parts = host.split('.')
    if (parts.size != 4) return false
    val bytes = parts.map { it.toIntOrNull() ?: return false }
    if (bytes.any { it !in 0..255 }) return false
    return bytes[0] == 10 ||
        bytes[0] == 192 && bytes[1] == 168 ||
        bytes[0] == 172 && bytes[1] in 16..31
}
