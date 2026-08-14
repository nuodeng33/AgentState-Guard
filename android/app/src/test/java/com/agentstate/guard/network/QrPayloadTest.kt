package com.agentstate.guard.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.security.MessageDigest

class QrPayloadTest {
    private val publicKey = "04"
    private val signingFingerprint = MessageDigest.getInstance("SHA-256")
        .digest(byteArrayOf(0x04))
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun uri(host: String = "192.168.1.21", expiry: Long = 2_000) =
        "agentstate://pair?v=1&host=$host&port=8788&uuid=desktop-1" +
            "&pub=$publicKey&sign_fp=$signingFingerprint&tls_fp=${"b".repeat(64)}" +
            "&sid=${"c".repeat(32)}&ticket=${"d".repeat(64)}&exp=$expiry"

    @Test
    fun parsesStrictPrivateLanInvitation() {
        val payload = QrPayload.parse(uri(), nowEpochSeconds = 1_000)
        assertNotNull(payload)
        assertEquals("192.168.1.21", payload?.endpoint?.host)
        assertEquals("d".repeat(64), payload?.ticket)
        assertEquals(signingFingerprint, payload?.desktopSigningFingerprint)
    }

    @Test fun rejectsExpired() = assertNull(QrPayload.parse(uri(expiry = 999), 1_000))
    @Test fun rejectsPublicAddress() = assertNull(QrPayload.parse(uri(host = "8.8.8.8"), 1_000))
    @Test fun rejectsMissingTicket() = assertNull(QrPayload.parse(uri().replace(Regex("&ticket=[^&]+"), ""), 1_000))
    @Test fun rejectsWrongScheme() = assertNull(QrPayload.parse("https://example.com", 1_000))
    @Test fun rejectsSigningFingerprintThatDoesNotMatchPublicKey() = assertNull(
        QrPayload.parse(uri().replace("sign_fp=$signingFingerprint", "sign_fp=${"0".repeat(64)}"), 1_000)
    )
}
