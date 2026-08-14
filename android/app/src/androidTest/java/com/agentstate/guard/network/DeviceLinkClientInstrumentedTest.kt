package com.agentstate.guard.network

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DeviceLinkClientInstrumentedTest {
    @Test
    fun androidKeyStoreP256KeySignsWithoutExportingPrivateMaterial() {
        val signer = AndroidKeyStoreSigner("agentstate-guard-instrumented-test")
        try {
            val publicKey = signer.publicKeyDer()
            val signature = signer.sign("device-link".toByteArray())
            assertTrue(publicKey.isNotEmpty())
            assertTrue(signature.isNotEmpty())
        } finally {
            signer.delete()
        }
    }
}
