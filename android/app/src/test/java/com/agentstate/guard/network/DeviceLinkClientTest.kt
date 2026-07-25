package com.agentstate.guard.network

import com.sun.net.httpserver.HttpServer
import com.sun.net.httpserver.HttpExchange
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*
import java.net.InetSocketAddress

/**
 * JVM unit tests for DeviceLinkClient using Java's built-in HTTP server.
 * No Android emulator needed. Tests verify the client sends correct HTTP
 * requests and parses JSON responses properly.
 */
class DeviceLinkClientTest {

    private var server: HttpServer? = null
    private var client: DeviceLinkClient? = null

    @Before
    fun setUp() {
        server = HttpServer.create(InetSocketAddress(0), 0)
        server?.executor = null  // use default single-thread executor
        server?.start()
        val port = server!!.address.port
        client = DeviceLinkClient("http://127.0.0.1:$port")
    }

    @After
    fun tearDown() {
        server?.stop(0)
    }

    // ── Pairing ─────────────────────────────────────────────────

    @Test
    fun pairStart_returnsSessionId() {
        server?.createContext("/device/v1/pair/start") { exchange ->
            assertPost(exchange)
            respond(exchange, 200,
                """{"session_id":"sid-001","desktop_uuid":"duuid","desktop_pubkey_fingerprint":"fp","state":"created"}""")
        }
        val result = client!!.pairStart()
        assertEquals("sid-001", result.sessionId)
        assertEquals("duuid", result.desktopUuid)
        assertEquals("fp", result.fingerprint)
        assertEquals("created", result.state)
    }

    @Test
    fun pairConnect_returnsState() {
        server?.createContext("/device/v1/pair/sid-001/connect") { exchange ->
            assertPost(exchange)
            assertBodyContains(exchange, "android_uuid")
            assertBodyContains(exchange, "nonce")
            respond(exchange, 200,
                """{"state":"first_connection"}""")
        }
        val state = client!!.pairConnect("sid-001", "android-e2e", "ab" + "ab".repeat(15))
        assertEquals("first_connection", state)
    }

    @Test
    fun pairSas_returnsSasCode() {
        server?.createContext("/device/v1/pair/sid-001/sas") { exchange ->
            assertPost(exchange)
            assertBodyContains(exchange, "android_pubkey_der_hex")
            respond(exchange, 200,
                """{"sas":"123 456","state":"sas_pending"}""")
        }
        val sas = client!!.pairSas("sid-001", "0474657374")
        assertEquals("123 456", sas)
    }

    @Test
    fun pairComplete_returnsBoundWithToken() {
        server?.createContext("/device/v1/pair/sid-001/complete") { exchange ->
            assertPost(exchange)
            assertBodyContains(exchange, "android_uuid")
            assertBodyContains(exchange, "android_pubkey_der_hex")
            assertBodyContains(exchange, "display_name")
            respond(exchange, 200,
                """{"status":"bound","session_token":"tok-001","desktop_uuid":"duuid","permissions":["read"]}""")
        }
        val status = client!!.pairComplete("sid-001", "android-e2e", "0474657374", "Test Device")
        assertEquals("bound", status)
    }

    // ── Read-only API ────────────────────────────────────────────

    @Test
    fun getStatus_returnsJson() {
        server?.createContext("/device/v1/status") { exchange ->
            assertEquals("GET", exchange.requestMethod)
            respond(exchange, 200,
                """{"status":"active","desktop_uuid":"duuid","bound_devices":1}""")
        }
        val json = client!!.getStatus()
        assertEquals("active", json.getString("status"))
        assertEquals(1, json.getInt("bound_devices"))
    }

    // ── Full flow ────────────────────────────────────────────────

    @Test
    fun fullPairingFlow_roundtrip() {
        var step = 0
        server?.createContext("/device/v1/") { exchange ->
            val path = exchange.requestURI.path
            val response = when {
                path == "/device/v1/pair/start" && step++ == 0 -> {
                    assertPost(exchange)
                    """{"session_id":"flow-sid","desktop_uuid":"duuid","desktop_pubkey_fingerprint":"fp","state":"created"}"""
                }
                path == "/device/v1/pair/flow-sid/connect" && step++ == 1 -> {
                    assertPost(exchange)
                    """{"state":"first_connection"}"""
                }
                path == "/device/v1/pair/flow-sid/sas" && step++ == 2 -> {
                    assertPost(exchange)
                    """{"sas":"654 321","state":"sas_pending"}"""
                }
                path == "/device/v1/pair/flow-sid/confirm" && step++ == 3 -> {
                    assertPost(exchange)
                    """{"state":"confirmed_both"}"""
                }
                path == "/device/v1/pair/flow-sid/complete" && step++ == 4 -> {
                    assertPost(exchange)
                    """{"status":"bound","session_token":"tok-flow","permissions":["read"]}"""
                }
                path == "/device/v1/status" && step++ == 5 -> {
                    assertEquals("GET", exchange.requestMethod)
                    """{"status":"active","desktop_uuid":"duuid"}"""
                }
                else -> {
                    respond(exchange, 404, """{"error":"no match","code":404}""")
                    return@createContext
                }
            }
            respond(exchange, 200, response)
        }

        val start = client!!.pairStart()
        assertEquals("flow-sid", start.sessionId)

        val conn = client!!.pairConnect("flow-sid", "a-uuid", "aa".repeat(16))
        assertEquals("first_connection", conn)

        val sas = client!!.pairSas("flow-sid", "0474657374")
        assertEquals("654 321", sas)

        val confirm = client!!.pairConfirm("flow-sid", confirm = true)
        assertEquals("confirmed_both", confirm)

        val bound = client!!.pairComplete("flow-sid", "a-uuid", "0474657374", "Flow Device")
        assertEquals("bound", bound)

        val status = client!!.getStatus()
        assertEquals("active", status.getString("status"))

        assertEquals(6, step)
    }

    // ── Helpers ──────────────────────────────────────────────────

    private fun assertPost(exchange: HttpExchange) {
        assertEquals("POST", exchange.requestMethod)
    }

    private fun assertBodyContains(exchange: HttpExchange, key: String) {
        val body = exchange.requestBody.readBytes().toString(Charsets.UTF_8)
        assertTrue("Body should contain '$key': $body", body.contains(key))
    }

    private fun respond(exchange: HttpExchange, code: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.set("Content-Type", "application/json")
        exchange.sendResponseHeaders(code, bytes.size.toLong())
        exchange.responseBody.write(bytes)
        exchange.close()
    }
}
