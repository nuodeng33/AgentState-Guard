package com.agentstate.guard.qr

import android.graphics.ImageFormat
import androidx.camera.core.ImageProxy

/**
 * Adapter from the CameraX analysis frame to the pure [QrFrame] contract.
 *
 * Only YUV_420_888 luminance (the Y plane) is used; unsupported formats fail
 * closed as an unreadable frame. Row stride is honoured so no garbage row
 * padding leaks into the matrix. This is the only Android-type-dependent
 * piece of the scanner: the analyzer, PreviewView, and permission flow sit
 * behind it in ConnectivityQrScanView.
 */
class CameraXQrFrame(private val proxy: ImageProxy) : QrFrame {
    override fun pixels(): QrPixelGrid? {
        if (proxy.format != ImageFormat.YUV_420_888) return null
        val plane = proxy.planes.firstOrNull() ?: return null
        val rowStride = plane.rowStride
        val buffer = plane.buffer
        val width = proxy.width
        val height = proxy.height
        if (width <= 0 || height <= 0 || rowStride < width) return null
        val samples = ByteArray(width * height)
        val row = ByteArray(rowStride)
        for (y in 0 until height) {
            buffer.position(y * rowStride)
            val length = minOf(rowStride, buffer.remaining())
            if (length < width) return null
            buffer.get(row, 0, length)
            System.arraycopy(row, 0, samples, y * width, width)
        }
        return QrPixelGrid(width, height, samples)
    }
}
