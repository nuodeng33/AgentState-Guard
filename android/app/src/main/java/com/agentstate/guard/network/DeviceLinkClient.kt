package com.agentstate.guard.network

import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Android client for Device Link Gateway. Read-only. */
class DeviceLinkClient(
    private val baseUrl: String = "http://10.0.2.2:8788",
    private val timeoutMs: Int = 15_000,
) {
    private var sessionToken: String? = null

    // ── Pairing ────────────────────────

    data class PairStart(val sessionId: String, val desktopUuid: String, val fingerprint: String, val state: String)

    fun pairStart(): PairStart {
        val json = post("/device/v1/pair/start", JSONObject())
        return PairStart(
            json.getString("session_id"),
            json.getString("desktop_uuid"),
            json.optString("desktop_pubkey_fingerprint", ""),
            json.optString("state", "created"),
        )
    }

    fun pairConnect(sessionId: String, androidUuid: String, nonce: String): String {
        val body = JSONObject().apply {
            put("android_uuid", androidUuid)
            put("nonce", nonce)
        }
        val json = post("/device/v1/pair/$sessionId/connect", body)
        return json.optString("state", "")
    }

    fun pairSas(sessionId: String, pubkeyDerHex: String): String {
        val body = JSONObject().apply {
            put("android_pubkey_der_hex", pubkeyDerHex)
        }
        val json = post("/device/v1/pair/$sessionId/sas", body)
        return json.getString("sas")
    }

    fun pairConfirm(sessionId: String, confirm: Boolean): String {
        val body = JSONObject().apply { put("confirm", confirm) }
        val json = post("/device/v1/pair/$sessionId/confirm", body)
        return json.optString("state", "")
    }

    fun pairComplete(sessionId: String, androidUuid: String, pubkeyDerHex: String, displayName: String): String {
        val body = JSONObject().apply {
            put("android_uuid", androidUuid)
            put("android_pubkey_der_hex", pubkeyDerHex)
            put("display_name", displayName)
        }
        val json = post("/device/v1/pair/$sessionId/complete", body)
        sessionToken = json.optString("session_token", null)
        return json.optString("status", "")
    }

    // ── Read-only API ────────────────────

    fun getStatus(): JSONObject = get("/device/v1/status")
    fun getEnvironment(): JSONObject = get("/device/v1/environment")
    fun getCheckpoints(): JSONObject = get("/device/v1/checkpoints")
    fun getDiff(): JSONObject = get("/device/v1/diff")
    fun getAIResult(): JSONObject = get("/device/v1/ai")

    // ── HTTP ─────────────────────────────

    private fun get(path: String): JSONObject = request(path, "GET", null)
    private fun post(path: String, body: JSONObject?): JSONObject = request(path, "POST", body)

    private fun request(path: String, method: String, body: JSONObject?): JSONObject {
        val url = URL("${baseUrl}${path}")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = timeoutMs
        conn.readTimeout = timeoutMs
        conn.setRequestProperty("Content-Type", "application/json")
        sessionToken?.let { conn.setRequestProperty("X-Session-Token", it) }
        if (body != null && method == "POST") {
            conn.doOutput = true
            conn.outputStream.write(body.toString().toByteArray())
        }
        val code = conn.responseCode
        val text = if (code in 200..299)
            conn.inputStream.bufferedReader().readText()
        else
            conn.errorStream?.bufferedReader()?.readText() ?: "{}"
        conn.disconnect()
        return JSONObject(text)
    }
}
