package com.agentstate.guard.network

import org.junit.Test
import org.junit.Assert.*

class QrPayloadTest {
    @Test
    fun parseValidUri() {
        val uri = "agentstate://pair?host=192.168.1.21&port=8788&uuid=desktop-uuid-1234&fp=a1b2c3d4&sid=session-abc&exp=9999999999"
        val payload = QrPayload.parse(uri)
        assertNotNull(payload)
        assertEquals("192.168.1.21", payload?.host)
        assertEquals(8788, payload?.port)
        assertEquals("desktop-uuid-1234", payload?.desktopUuid)
        assertEquals("a1b2c3d4", payload?.fingerprint)
        assertEquals("session-abc", payload?.sessionId)
        assertEquals(9999999999L, payload?.expiry)
    }

    @Test
    fun parseInvalidScheme() {
        assertNull(QrPayload.parse("https://example.com"))
    }

    @Test
    fun parseMissingRequiredField() {
        assertNull(QrPayload.parse("agentstate://pair?host=192.168.1.21&port=8788"))
    }

    @Test
    fun parseCustomPort() {
        val uri = "agentstate://pair?host=10.0.2.2&port=8790&uuid=desktop-1&fp=abcd1234&sid=sess-001&exp=8888"
        val payload = QrPayload.parse(uri)
        assertNotNull(payload)
        assertEquals(8790, payload?.port)
    }

    @Test
    fun parseEmptyUri() {
        assertNull(QrPayload.parse(""))
    }
}
