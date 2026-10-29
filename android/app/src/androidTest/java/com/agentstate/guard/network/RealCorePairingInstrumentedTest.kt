package com.agentstate.guard.network

import android.content.Context
import android.os.Process
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.agentstate.guard.ui.link.decodeInvitationImage
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.KeyStore
import java.util.UUID

/** Opt-in real-Core protocol fixture: no Activity or UI-input injection.
 * Normal CI skips the external fixture. Optical camera/UI acceptance is separate.
 */
@RunWith(AndroidJUnit4::class)
class RealCorePairingInstrumentedTest {
    @Test
    fun actualCorePairReconnectRevokeWithAndroidKeyStore() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("asgFixture")
        assumeTrue("Explicit owned Core fixture required", path != null)
        require(path!!.matches(Regex("^/data/local/tmp/asg-device-[0-9a-f]{32}[.]json$")))
        val input = File(path)
        require(input.canonicalPath == path && input.length() in 1L..16384L)
        val fixture = JSONObject(input.readText())
        val nonce = fixture.getString("nonce")
        require(nonce.matches(Regex("^[0-9a-f]{32}$")))
        val stage = args.getString("asgStage") ?: "pair"
        require(stage in setOf("pair", "resume", "cleanup"))
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        require(context.packageName == "com.agentstate.guard.acceptance")
        val statusFile = File(context.filesDir, "asg-protocol-$nonce.json")
        val commandFile = File("/data/local/tmp/asg-device-$nonce-command.json")
        val preferences = context.getSharedPreferences("asg-protocol-$nonce", Context.MODE_PRIVATE)
        val store = SharedPreferencesBindingStore(preferences)
        val alias = "asg-protocol-$nonce"
        val signer = AndroidKeyStoreSigner(alias)
        val androidUuid = store.load()?.androidUuid ?: UUID.randomUUID().toString()
        val checks = JSONObject()
        fun status(phase: String, sas: String? = null) {
            val value = JSONObject().put("phase", phase).put("nonce", nonce)
                .put("android_uuid", androidUuid).put("checks", checks)
                .put("process_id", Process.myPid()).put("process_started_elapsed_ms", Process.getStartElapsedRealtime())
            if (sas != null) value.put("sas", sas)
            val temporary = File(statusFile.path + ".tmp")
            temporary.writeText(value.toString())
            require(temporary.renameTo(statusFile))
        }
        fun await(phase: String) {
            val end = System.currentTimeMillis() + 90000
            while (System.currentTimeMillis() < end) {
                try {
                    if (commandFile.isFile && commandFile.length() in 1L..1024L && commandFile.canonicalPath == commandFile.path) {
                        val command = JSONObject(commandFile.readText())
                        if (command.optString("nonce") == nonce && command.optString("phase") == phase) return
                    }
                } catch (_: org.json.JSONException) { /* Host command not yet atomically visible. */ }
                Thread.sleep(100)
            }
            throw AssertionError("Owned Core fixture phase timeout: $phase")
        }
        val repository = DeviceLinkRepository(store, signer, BoundDeviceRediscovery())
        var retainForRestart = false
        try {
            if (stage == "cleanup") {
                repository.unpairLocal()
                status("CLEANED")
                return
            }
            if (stage == "pair") {
                require(store.load() == null) { "Refuse to replace an existing fixture binding" }
                var uri = fixture.getString("qr_uri")
                if (fixture.has("qr_png")) {
                    val image = File(fixture.getString("qr_png"))
                    require(image.canonicalPath == "/data/local/tmp/asg-device-$nonce.png")
                    val decoded = image.inputStream().use { decodeInvitationImage(it) }
                    require(decoded.payload == uri) { "Actual invitation image did not decode exactly" }
                    uri = requireNotNull(decoded.payload)
                    checks.put("actual_qr_image_decoded", true)
                }
                val payload = requireNotNull(QrPayload.parse(uri))
                repository.beginPairing(payload, androidUuid)
                val sas = repository.startSas(payload)
                require(sas.matches(Regex("^[0-9]{3} [0-9]{3}$")))
                repository.confirmSas(payload, true)
                status("ANDROID_SAS_CONFIRMED", sas)
                await("DESKTOP_SAS_CONFIRMED")
                assertEquals("bound", repository.completePairing(payload, androidUuid, "MuMu1 protocol acceptance"))
                checks.put("binding_saved", store.load()?.androidUuid == androidUuid)
                val first = repository.refresh()
                checks.put("real_tls_authenticated_reads", first.online && first.status != null && first.agents != null && first.environment != null && first.supervision != null && first.changes != null && first.checkpoints != null && first.recovery != null && first.aiAdvisory != null)
                require(checks.getBoolean("binding_saved") && checks.getBoolean("real_tls_authenticated_reads"))
                status("ONLINE")
                await("CORE_STOPPED")
                val offline = repository.refresh()
                checks.put("offline_retains_last_known", !offline.online && offline.status?.toString() == first.status?.toString())
                require(checks.getBoolean("offline_retains_last_known"))
                status("OFFLINE")
                await("CORE_RESTARTED")
                checks.put("core_restart_reauthenticates", repository.refresh().online)
                require(checks.getBoolean("core_restart_reauthenticates"))
                // Flush this unique test namespace before the runner process exits.
                require(preferences.edit().putBoolean("test_disk_flush", true).commit())
                status("READY_FOR_PROCESS_RESTART")
                retainForRestart = true
                return
            }
            require(store.load() != null) { "No durable fixture binding after process restart" }
            checks.put("android_process_restart_reauthenticates", repository.refresh().online)
            require(checks.getBoolean("android_process_restart_reauthenticates"))
            status("PROCESS_RESTART_ONLINE")
            await("DESKTOP_REVOKED")
            val revoked = repository.refresh()
            checks.put("revocation_requires_auth", !revoked.online && revoked.authRequired)
            require(checks.getBoolean("revocation_requires_auth"))
            repository.unpairLocal()
            checks.put("local_unpair_clears_binding", store.load() == null)
            val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            checks.put("local_unpair_removes_key", !keys.containsAlias(alias))
            require(checks.getBoolean("local_unpair_clears_binding") && checks.getBoolean("local_unpair_removes_key"))
            status("PASS")
        } catch (failure: Throwable) {
            status("FAIL")
            throw failure
        } finally {
            // Only this fixture namespace/key, never the app-default or old app.
            if (!retainForRestart) {
                store.clear()
                signer.delete()
            }
        }
    }
}
