package com.agentstate.guard.ui

import com.agentstate.guard.network.QrPayload
import com.agentstate.guard.ui.link.MAX_QR_IMAGE_BYTES
import com.agentstate.guard.ui.link.QrImageFailure
import com.agentstate.guard.ui.link.decodeQrImagePixels
import com.agentstate.guard.ui.link.qrImageSampleSize
import com.agentstate.guard.ui.link.readBoundedQrImage
import com.google.zxing.BarcodeFormat
import com.google.zxing.oned.Code128Writer
import com.google.zxing.qrcode.QRCodeWriter
import java.io.ByteArrayInputStream
import java.io.InputStream
import org.junit.Assert.*
import org.junit.Test
import java.io.DataInputStream
import java.security.DigestInputStream
import java.security.MessageDigest
import java.util.zip.GZIPInputStream
import org.json.JSONObject

class InvitationImageDecoderTest {
    @Test fun realBrowserDenseInvitationRastersRoundTripAcrossSizesAndDisplayScales() {
        fun resource(name: String) = javaClass.getResourceAsStream("/pairing-qr/$name")!!
        fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) }
        fun sha256(bytes: ByteArray) = hex(MessageDigest.getInstance("SHA-256").digest(bytes))
        val expected = resource("invitation.txt").bufferedReader(Charsets.UTF_8).use { it.readText() }
        val manifest = JSONObject(resource("argb-manifest.json").bufferedReader().use { it.readText() })
            .getJSONObject("files")
        assertEquals(547, expected.length)
        for (cell in listOf(3, 4, 5)) for (dpr in listOf("1", "1_25", "1_5", "2")) {
            val stem = "dense-cell$cell-dpr$dpr"
            val metadata = manifest.getJSONObject("$stem.png")
            assertEquals(stem, metadata.getString("png_sha256"), sha256(resource("$stem.png").use { it.readBytes() }))
            val compressed = resource("$stem.argb.gz").use { it.readBytes() }
            assertEquals(stem, metadata.getString("gzip_sha256"), sha256(compressed))
            val digest = MessageDigest.getInstance("SHA-256")
            // Exact browser ARGB values, losslessly exported: no Android-incompatible
            // Java desktop ImageIO dependency and no change to the product decoder.
            val (width, height, pixels) = DataInputStream(DigestInputStream(
                GZIPInputStream(ByteArrayInputStream(compressed)), digest
            )).use { input ->
                assertEquals(stem, 0x41534731, input.readInt())
                val width = input.readInt()
                val height = input.readInt()
                assertEquals(stem, metadata.getInt("width"), width)
                assertEquals(stem, metadata.getInt("height"), height)
                assertTrue(stem, width in 1..2048 && height in 1..2048 && width.toLong() * height <= 4_194_304)
                val pixels = IntArray(width * height) { input.readInt() }
                assertEquals(stem, -1, input.read()) // Also validates the gzip trailer/CRC.
                Triple(width, height, pixels)
            }
            assertEquals(stem, metadata.getString("raw_stream_sha256"), hex(digest.digest()))
            val result = decodeQrImagePixels(pixels, width, height)
            assertEquals(stem, expected, result.payload)
            assertNull(stem, result.reasonCode)
        }
        // Pixel decoding supplies text, never authority: this synthetic offer is expired.
        assertNull(QrPayload.parse(expected, nowEpochSeconds = 1_000_000_001L))
    }

    @Test fun bundledDecoderReturnsQrPayloadExactlyWithoutInferringTrust() {
        val payload = "agentstate://pair?v=1&sid=exact-opaque-value&ticket=not-authenticated-by-decoder"
        val matrix = QRCodeWriter().encode(payload, BarcodeFormat.QR_CODE, 512, 512)
        val pixels = IntArray(matrix.width * matrix.height) { index ->
            if (matrix[index % matrix.width, index / matrix.width]) 0xff000000.toInt() else 0xffffffff.toInt()
        }
        val result = decodeQrImagePixels(pixels, matrix.width, matrix.height)
        assertEquals(payload, result.payload)
        assertNull(result.reasonCode)
    }

    @Test fun otherBarcodeFormatsAreNotAcceptedAsInvitations() {
        val matrix = Code128Writer().encode("123456789012", BarcodeFormat.CODE_128, 800, 200)
        val pixels = IntArray(matrix.width * matrix.height) { index ->
            if (matrix[index % matrix.width, index / matrix.width]) 0xff000000.toInt() else 0xffffffff.toInt()
        }
        val result = decodeQrImagePixels(pixels, matrix.width, matrix.height)
        assertNull(result.payload)
        assertEquals("QR_IMAGE_NO_CODE", result.reasonCode)
    }

    @Test fun imageStreamIsBoundedBeforeDecodeEvenWithoutTrustedMetadata() {
        val input = object : InputStream() {
            var consumed = 0
            override fun read(): Int { consumed++; return 0 }
            override fun read(bytes: ByteArray, offset: Int, length: Int): Int {
                java.util.Arrays.fill(bytes, offset, offset + length, 0.toByte())
                consumed += length
                return length
            }
        }
        try {
            readBoundedQrImage(input)
            fail("Unbounded input accepted")
        } catch (failure: QrImageFailure) {
            assertEquals("QR_IMAGE_TOO_LARGE", failure.reasonCode)
        }
        assertEquals(MAX_QR_IMAGE_BYTES + 1, input.consumed)
        assertArrayEquals(byteArrayOf(1, 2, 3), readBoundedQrImage(ByteArrayInputStream(byteArrayOf(1, 2, 3))))
    }

    @Test fun hugeOrInvalidDimensionsAreRejectedAndLargePhotosAreSampled() {
        assertEquals(4, qrImageSampleSize(8000, 8000))
        assertEquals(1, qrImageSampleSize(800, 600))
        for ((width, height) in listOf(9000 to 9000, 20000 to 1, 0 to 100)) {
            try {
                qrImageSampleSize(width, height)
                fail("Unsafe dimensions accepted")
            } catch (_: QrImageFailure) { }
        }
    }
}
