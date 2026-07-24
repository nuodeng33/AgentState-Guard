package com.agentstate.guard.network

/** Parses agentstate://pair URIs from QR codes. */
data class QrPayload(
    val host: String,
    val port: Int,
    val desktopUuid: String,
    val fingerprint: String,
    val sessionId: String,
    val expiry: Long,
) {
    companion object {
        fun parse(uri: String): QrPayload? {
            if (!uri.startsWith("agentstate://pair?")) return null
            val params = uri.substringAfter("?").split("&").associate {
                val parts = it.split("=", limit = 2)
                parts[0] to (parts.getOrNull(1) ?: "")
            }
            return try {
                QrPayload(
                    host = params["host"] ?: return null,
                    port = params["port"]?.toInt() ?: 8788,
                    desktopUuid = params["uuid"] ?: return null,
                    fingerprint = params["fp"] ?: "",
                    sessionId = params["sid"] ?: return null,
                    expiry = params["exp"]?.toLong() ?: 0,
                )
            } catch (e: NumberFormatException) { null }
        }
    }
}
