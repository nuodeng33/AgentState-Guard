package com.agentstate.guard.ui

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

class InvitationImageDecoderTest {
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
