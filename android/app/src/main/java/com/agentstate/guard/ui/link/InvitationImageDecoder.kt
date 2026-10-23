package com.agentstate.guard.ui.link

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.NotFoundException
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.ReaderException
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.io.ByteArrayOutputStream
import java.io.InputStream

internal const val MAX_QR_IMAGE_BYTES = 16 * 1024 * 1024
internal const val MAX_QR_SOURCE_PIXELS = 64_000_000L
internal const val MAX_QR_BITMAP_SIDE = 2048
internal const val MAX_QR_BITMAP_PIXELS = MAX_QR_BITMAP_SIDE * MAX_QR_BITMAP_SIDE

internal class QrImageFailure(val reasonCode: String) : Exception()
internal data class QrImageReadResult(val payload: String? = null, val reasonCode: String? = null)

/** Reads only the selected document, bounded before allocating a decoded bitmap. */
internal fun readBoundedQrImage(input: InputStream): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(16 * 1024)
    var total = 0
    while (true) {
        val count = input.read(buffer, 0, minOf(buffer.size, MAX_QR_IMAGE_BYTES - total + 1))
        if (count < 0) break
        if (count == 0) continue
        total += count
        if (total > MAX_QR_IMAGE_BYTES) throw QrImageFailure("QR_IMAGE_TOO_LARGE")
        output.write(buffer, 0, count)
    }
    if (total == 0) throw QrImageFailure("QR_IMAGE_INVALID")
    return output.toByteArray()
}

internal fun qrImageSampleSize(width: Int, height: Int): Int {
    if (width <= 0 || height <= 0) throw QrImageFailure("QR_IMAGE_INVALID")
    if (width > 16384 || height > 16384 || width.toLong() * height > MAX_QR_SOURCE_PIXELS) {
        throw QrImageFailure("QR_IMAGE_TOO_LARGE")
    }
    var sample = 1
    while ((width + sample - 1) / sample > MAX_QR_BITMAP_SIDE ||
        (height + sample - 1) / sample > MAX_QR_BITMAP_SIDE ||
        ((width + sample - 1) / sample).toLong() * ((height + sample - 1) / sample) > MAX_QR_BITMAP_PIXELS) {
        sample *= 2
    }
    return sample
}

/** QR-only decoder; returned text is passed unchanged to the existing strict invitation parser. */
internal fun decodeQrImagePixels(pixels: IntArray, width: Int, height: Int): QrImageReadResult {
    if (width <= 0 || height <= 0 || width > MAX_QR_BITMAP_SIDE || height > MAX_QR_BITMAP_SIDE ||
        width.toLong() * height > MAX_QR_BITMAP_PIXELS || pixels.size.toLong() != width.toLong() * height) {
        return QrImageReadResult(reasonCode = "QR_IMAGE_INVALID")
    }
    return try {
        val bitmap = BinaryBitmap(HybridBinarizer(RGBLuminanceSource(width, height, pixels)))
        val result = QRCodeReader().decode(bitmap, mapOf(DecodeHintType.TRY_HARDER to true))
        if (result.text.isNullOrBlank()) QrImageReadResult(reasonCode = "QR_IMAGE_NO_CODE")
        else QrImageReadResult(payload = result.text)
    } catch (_: NotFoundException) {
        QrImageReadResult(reasonCode = "QR_IMAGE_NO_CODE")
    } catch (_: ReaderException) {
        QrImageReadResult(reasonCode = "QR_IMAGE_NO_CODE")
    }
}

internal fun decodeInvitationImage(input: InputStream): QrImageReadResult {
    var bitmap: Bitmap? = null
    return try {
        val bytes = readBoundedQrImage(input)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        val sample = qrImageSampleSize(bounds.outWidth, bounds.outHeight)
        val options = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
            inScaled = false
        }
        val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
            ?: return QrImageReadResult(reasonCode = "QR_IMAGE_INVALID")
        bitmap = decoded
        if (decoded.width > MAX_QR_BITMAP_SIDE || decoded.height > MAX_QR_BITMAP_SIDE ||
            decoded.width.toLong() * decoded.height > MAX_QR_BITMAP_PIXELS) {
            return QrImageReadResult(reasonCode = "QR_IMAGE_TOO_LARGE")
        }
        val pixels = IntArray(decoded.width * decoded.height)
        decoded.getPixels(pixels, 0, decoded.width, 0, 0, decoded.width, decoded.height)
        decodeQrImagePixels(pixels, decoded.width, decoded.height)
    } catch (failure: QrImageFailure) {
        QrImageReadResult(reasonCode = failure.reasonCode)
    } catch (_: OutOfMemoryError) {
        QrImageReadResult(reasonCode = "QR_IMAGE_TOO_LARGE")
    } catch (_: Exception) {
        QrImageReadResult(reasonCode = "QR_IMAGE_UNREADABLE")
    } finally {
        bitmap?.recycle()
    }
}
