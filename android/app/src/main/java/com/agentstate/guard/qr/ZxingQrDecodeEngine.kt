package com.agentstate.guard.qr

import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.LuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.multi.qrcode.QRCodeMultiReader

/**
 * ZXing-backed production engine (no Android dependencies, JVM-unit-testable
 * with the real decoder binary). Unknown decode failures return null — the
 * camera loop simply keeps scanning; engine errors never reach the UI as
 * fake payloads or raw exceptions.
 */
class ZxingQrDecodeEngine : QrDecodeEngine {
    override fun decode(frame: QrFrame): String? {
        val grid = frame.pixels() ?: return null
        val width = grid.width
        val height = grid.height
        if (width < QR_MIN_DIMENSION || height < QR_MIN_DIMENSION) return null
        // Finder-pattern geometry makes QR detection orientation invariant;
        // a single upright pass is sufficient and avoids rotation support
        // variance across LuminanceSource implementations.
        val source = GridLuminanceSource(grid)
        return try {
            val result = QRCodeMultiReader()
                .decodeMultiple(BinaryBitmap(HybridBinarizer(source))) ?: return null
            val text = result.firstOrNull { it.barcodeFormat == BarcodeFormat.QR_CODE }
                ?.text ?: result.firstOrNull()?.text
            text?.takeIf { it.isNotBlank() }
        } catch (_: com.google.zxing.NotFoundException) {
            null // No symbol in frame; keep scanning.
        } catch (_: com.google.zxing.FormatException) {
            null // Partial/mis-framed symbol; keep scanning.
        } catch (_: com.google.zxing.ChecksumException) {
            null // Corrupted symbol; keep scanning.
        }
    }

    private class GridLuminanceSource(private val grid: QrPixelGrid) :
        LuminanceSource(grid.width, grid.height) {
        override fun getRow(y: Int, row: ByteArray?): ByteArray {
            val target = if (row != null && row.size >= width) row else ByteArray(width)
            System.arraycopy(grid.samples, y * width, target, 0, width)
            return target
        }

        override fun getMatrix(): ByteArray = grid.samples

        override fun isCropSupported(): Boolean = false
        override fun isRotateSupported(): Boolean = false
    }

    private companion object {
        /** Below this the finder pattern cannot resolve; fail cheap. */
        const val QR_MIN_DIMENSION = 21
    }
}
