package com.agentstate.guard.network

import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** Exact-UUID UDP rediscovery; discovery never grants trust. */
class BoundDeviceRediscovery(private val timeoutMs: Int = 1_500) : DeviceEndpointRediscovery {
    override fun rediscover(binding: BoundDesktop): DeviceEndpoint? = try {
        DatagramSocket().use { socket ->
            socket.broadcast = true
            socket.soTimeout = timeoutMs
            val request = JSONObject()
                .put("protocol", "ASDL_DISCOVERY_1")
                .put("desktop_uuid", binding.desktopUuid)
                .toString().toByteArray(Charsets.UTF_8)
            socket.send(DatagramPacket(
                request, request.size, InetAddress.getByName("255.255.255.255"), 8788
            ))
            val buffer = ByteArray(1024)
            val response = DatagramPacket(buffer, buffer.size)
            socket.receive(response)
            val json = JSONObject(String(response.data, 0, response.length, Charsets.UTF_8))
            if (
                json.optString("protocol") != "ASDL_DISCOVERY_1" ||
                json.optString("desktop_uuid") != binding.desktopUuid ||
                json.optString("tls_spki_fingerprint") != binding.endpoint.tlsSpkiFingerprint
            ) return null
            DeviceEndpoint(
                response.address.hostAddress ?: return null,
                json.optInt("port", 0),
                json.getString("tls_spki_fingerprint"),
            )
        }
    } catch (_: Exception) {
        null
    }
}
