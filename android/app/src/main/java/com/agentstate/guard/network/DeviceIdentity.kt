package com.agentstate.guard.network

import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.UUID

interface DeviceSigner {
    fun publicKeyDer(): ByteArray
    fun sign(message: ByteArray): ByteArray
    fun verifyDesktop(publicKeyDer: ByteArray, message: ByteArray, signature: ByteArray): Boolean
    fun delete()
}

/** Hardware-backed when available; private keys are never exportable. */
class AndroidKeyStoreSigner(private val alias: String) : DeviceSigner {
    private val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun ensureKey() {
        if (keyStore.containsAlias(alias)) return
        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore"
        )
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(java.security.spec.ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build()
        )
        generator.generateKeyPair()
    }

    override fun publicKeyDer(): ByteArray {
        ensureKey()
        return keyStore.getCertificate(alias).publicKey.encoded
    }

    override fun sign(message: ByteArray): ByteArray {
        ensureKey()
        val signer = Signature.getInstance("SHA256withECDSA")
        signer.initSign(keyStore.getKey(alias, null) as java.security.PrivateKey)
        signer.update(message)
        return signer.sign()
    }

    override fun verifyDesktop(
        publicKeyDer: ByteArray, message: ByteArray, signature: ByteArray
    ): Boolean = try {
        val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKeyDer))
        val verifier = Signature.getInstance("SHA256withECDSA")
        verifier.initVerify(publicKey)
        verifier.update(message)
        verifier.verify(signature)
    } catch (_: java.security.GeneralSecurityException) {
        false
    }

    override fun delete() {
        if (keyStore.containsAlias(alias)) keyStore.deleteEntry(alias)
    }
}

data class BoundDesktop(
    val endpoint: DeviceEndpoint,
    val desktopUuid: String,
    val desktopPublicKeyDerHex: String,
    val desktopSigningFingerprint: String,
    val androidUuid: String,
)

interface BindingStore {
    fun load(): BoundDesktop?
    fun save(binding: BoundDesktop)
    fun clear()
}

class SharedPreferencesBindingStore(private val preferences: SharedPreferences) : BindingStore {
    override fun load(): BoundDesktop? {
        val host = preferences.getString("host", null) ?: return null
        return try {
            BoundDesktop(
                DeviceEndpoint(
                    host,
                    preferences.getInt("port", 8788),
                    preferences.getString("tls_fp", null) ?: return null,
                ),
                preferences.getString("desktop_uuid", null) ?: return null,
                preferences.getString("desktop_pub", null) ?: return null,
                preferences.getString("sign_fp", null) ?: return null,
                preferences.getString("android_uuid", null) ?: return null,
            )
        } catch (_: IllegalArgumentException) {
            null
        }
    }

    override fun save(binding: BoundDesktop) {
        preferences.edit()
            .putString("host", binding.endpoint.host).putInt("port", binding.endpoint.port)
            .putString("tls_fp", binding.endpoint.tlsSpkiFingerprint)
            .putString("desktop_uuid", binding.desktopUuid)
            .putString("desktop_pub", binding.desktopPublicKeyDerHex)
            .putString("sign_fp", binding.desktopSigningFingerprint)
            .putString("android_uuid", binding.androidUuid).apply()
    }

    override fun clear() { preferences.edit().clear().apply() }

    companion object {
        fun newAndroidUuid(): String = UUID.randomUUID().toString()
    }
}
