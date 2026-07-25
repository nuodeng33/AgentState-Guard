package com.agentstate.guard.network

import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket

/**
 * JVM unit tests for DeviceLinkClient using a minimal ServerSocket-based
 * mock HTTP server. Only uses Android-compatible Java APIs.
 */
class DeviceLinkClientTest {

    private var mockServer: MockHttpServer? = null
    private var client: DeviceLinkClient? = null

    @Before
    fun setUp() {
        mockServer = MockHttpServer()
        mockServer?.start()
        val port = mockServer!!.port
        client = DeviceLinkClient("http://127.0.0.1:$port")
    }

    @After
    fun tearDown() {
        mockServer?.stop()
    }

    @Test
    fun pairStart_returnsSessionId() {
        mockServer?.nextResponse = """
            {"session_id":"sid-001","desktop_uuid":"duuid","desktop_pubkey_fingerprint":"fp","state":"created"}
        """.trimIndent()
        val result = client!!.pairStart()
        assertEquals("sid-001", result.sessionId)
        assertEquals("duuid", result.desktopUuid)
        assertEquals("fp", result.fingerprint)
        assertEquals("created", result.state)
    }

    @Test
    fun pairConnect_returnsState() {
        mockServer?.nextResponse = """{"state":"first_connection"}"""
        val state = client!!.pairConnect("sid-001", "android-e2e", "ab" + "ab".repeat(15))
        assertEquals("first_connection", state)
    }

    @Test
    fun pairSas_returnsSasCode() {
        mockServer?.nextResponse = """{"sas":"123 456","state":"sas_pending"}"""
        val sas = client!!.pairSas("sid-001", "0474657374")
        assertEquals("123 456", sas)
    }

    @Test
    fun pairComplete_returnsBoundWithToken() {
        mockServer?.nextResponse = """{"status":"bound","session_token":"tok-001","desktop_uuid":"duuid","permissions":["read"]}"""
        val status = client!!.pairComplete("sid-001", "android-e2e", "0474657374", "Test Device")
        assertEquals("bound", status)
    }

    @Test
    fun getStatus_returnsJson() {
        mockServer?.nextResponse = """{"status":"active","desktop_uuid":"duuid","bound_devices":1}"""
        val json = client!!.getStatus()
        assertEquals("active", json.getString("status"))
        assertEquals(1, json.getInt("bound_devices"))
    }

    @Test
    fun pairReject_returnsState() {
        mockServer?.nextResponse = """{"state":"rejected"}"""
        val state = client!!.pairConfirm("sid-001", confirm = false)
        assertEquals("rejected", state)
    }
}


/**
 * Minimal mock HTTP server using only Android-compatible Java APIs.
 * Responds to every request with a pre-configured JSON body + 200 OK.
 */
class MockHttpServer {
    private var serverSocket: ServerSocket? = null
    private var running = false
    private var thread: Thread? = null

    @Volatile
    var nextResponse: String = """{"status":"ok"}"""
    @Volatile
    var lastRequestMethod: String = ""
    @Volatile
    var lastRequestBody: String = ""
    @Volatile
    var lastRequestPath: String = ""

    val port: Int
        get() = serverSocket?.localPort ?: -1

    fun start() {
        serverSocket = ServerSocket(0)
        running = true
        thread = Thread {
            while (running) {
                try {
                    val client = serverSocket!!.accept()
                    handleClient(client)
                } catch (_: Exception) {
                    if (!running) break
                }
            }
        }
        thread!!.setDaemon(true)
        thread!!.start()
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        thread?.join(1000)
    }

    private fun handleClient(client: Socket) {
        try {
            client.use { socket ->
                val reader = BufferedReader(InputStreamReader(socket.getInputStream(), "UTF-8"))

                // Parse request line
                val requestLine = reader.readLine() ?: return
                val parts = requestLine.split(" ")
                lastRequestMethod = parts.getOrElse(0) { "" }
                lastRequestPath = parts.getOrElse(1) { "" }

                // Read headers
                var bodyLength = 0
                while (true) {
                    val header = reader.readLine() ?: break
                    if (header.isEmpty()) break
                    if (header.lowercase().startsWith("content-length:")) {
                        bodyLength = header.substringAfter(":").trim().toIntOrNull() ?: 0
                    }
                }

                // Read body
                if (bodyLength > 0) {
                    val body = CharArray(bodyLength)
                    reader.read(body, 0, bodyLength)
                    lastRequestBody = String(body)
                }

                // Send response
                val responseBytes = nextResponse.toByteArray(Charsets.UTF_8)
                val output = socket.getOutputStream()
                output.write("HTTP/1.1 200 OK\r\n".toByteArray())
                output.write("Content-Type: application/json\r\n".toByteArray())
                output.write("Content-Length: ${responseBytes.size}\r\n".toByteArray())
                output.write("Connection: close\r\n".toByteArray())
                output.write("\r\n".toByteArray())
                output.write(responseBytes)
                output.flush()
            }
        } catch (_: Exception) {
            // Client disconnected — not an error
        }
    }
}
