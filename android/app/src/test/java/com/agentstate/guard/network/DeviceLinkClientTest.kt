package com.agentstate.guard.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceLinkClientTest {
    private val endpoint = DeviceEndpoint("192.168.1.9", 8788, "a".repeat(64))

    @Test
    fun pairingTicketAndScopedTokenAreSentThenSessionStaysInMemory() {
        val transport = FakeTransport(
            JSONObject().put("state", "first_connection").put("pairing_token", "p".repeat(64)),
            JSONObject().put("sas", "123 456").put("state", "sas_pending"),
            JSONObject().put("state", "confirmed_both"),
            JSONObject().put("status", "bound").put("session_token", "s".repeat(64)),
            JSONObject().put("status", "active"),
        )
        val client = DeviceLinkClient(endpoint, transport)
        val payload = QrPayload(
            1, endpoint, "desktop", "04", "b".repeat(64),
            "1".repeat(32), "2".repeat(64), Long.MAX_VALUE,
        )

        assertEquals("first_connection", client.pairConnect(payload, "android-a", "3".repeat(64)))
        assertEquals("123 456", client.pairSas(payload.sessionId, "04"))
        assertEquals("confirmed_both", client.pairConfirm(payload.sessionId, true))
        assertEquals("bound", client.pairComplete(payload.sessionId, "android-a", "04", "Phone"))
        client.getStatus()

        assertEquals(payload.ticket, transport.requests[0].body?.getString("ticket"))
        assertEquals("p".repeat(64), transport.requests[1].headers["X-Pairing-Token"])
        assertEquals("Bearer ${"s".repeat(64)}", transport.requests.last().headers["Authorization"])
        assertTrue(transport.requests.none { it.endpoint.baseUrl.contains("10.0.2.2") })
    }

    @Test(expected = IllegalArgumentException::class)
    fun endpointCannotBeEmptyOrNonProductPort() {
        DeviceEndpoint("", 8790, "a".repeat(64))
    }

    @Test(expected = IllegalArgumentException::class)
    fun endpointCannotUsePublicInternetAddress() {
        DeviceEndpoint("8.8.8.8", 8788, "a".repeat(64))
    }
}

data class RecordedRequest(
    val endpoint: DeviceEndpoint,
    val path: String,
    val method: String,
    val body: JSONObject?,
    val headers: Map<String, String>,
)

class FakeTransport(vararg responses: JSONObject) : DeviceHttpTransport {
    private val responses = ArrayDeque(responses.toList())
    val requests = mutableListOf<RecordedRequest>()

    override fun request(
        endpoint: DeviceEndpoint,
        path: String,
        method: String,
        body: JSONObject?,
        headers: Map<String, String>,
    ): JSONObject {
        requests += RecordedRequest(endpoint, path, method, body, headers)
        return responses.removeFirst()
    }
}
