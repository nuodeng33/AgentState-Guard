package com.agentstate.guard.qr

/**
 * Pure, dependency-free QR decoder with an injectable bit-matrix engine.
 *
 * The production factory binds [com.agentstate.guard.scan.AndroidQrDecodeEngine]
 * (ZXing over CameraX frames), which is addVariants-only because it needs
 * android.graphics.Bitmap; the JVM unit tests bind a PIL-backed subprocess
 * decoder via the ASG_TEST_QR_ENGINE_COMMAND environment variable. Every
 * transport boundary — image in, text out, malformed payloads to null —
 * is exercised on the JVM against real encoded frames regardless of which
 * engine is bound.
 */
fun interface QrFrame {
    /** Returns the pixel grid, or null when the frame cannot be read. */
    fun pixels(): QrPixelGrid?
}

interface QrDecodeEngine {
    /** Decodes one frame; null when no QR code is present or readable. */
    fun decode(frame: QrFrame): String?
}

/** A luminance grid in row-major order; [width] × [height] samples. */
class QrPixelGrid(val width: Int, val height: Int, samples: ByteArray) {
    val samples: ByteArray = samples.copyOf()

    init {
        require(width >= 1 && height >= 1) { "empty grid" }
        require(samples.size == width * height) { "sample count must equal width*height" }
    }

    override fun equals(other: Any?): Boolean =
        other is QrPixelGrid &&
            width == other.width && height == other.height &&
            samples.contentEquals(other.samples)

    override fun hashCode(): Int = 31 * (31 * width + height) + samples.contentHashCode()
}

/** Cross-process engine for JVM tests: PNG bytes on stdin, text on stdout. */
class SubprocessQrDecodeEngine(private val commandLine: String) : QrDecodeEngine {
    override fun decode(frame: QrFrame): String? {
        val process = ProcessBuilder(commandLine.split(' '))
            .redirectErrorStream(true)
            .start()
        process.outputStream.use { it.write(PngEncoder.encode(frame)) }
        val output = process.inputStream.readBytes().toString(Charsets.UTF_8).trim()
        return if (process.waitFor() == 0 && output.isNotEmpty()) output else null
    }
}

/** Encodes a decoded grid as a grayscale 8-bit PNG (single IDAT). */
object PngEncoder {
    fun encode(frame: QrFrame): ByteArray {
        val grid = frame.pixels() ?: return ByteArray(0)
        val raw = ByteArray((grid.width + 1) * grid.height)
        for (row in 0 until grid.height) {
            raw[row * (grid.width + 1)] = 0
            System.arraycopy(
                grid.samples, row * grid.width, raw, row * (grid.width + 1) + 1, grid.width,
            )
        }
        return png(grid.width, grid.height, raw)
    }

    private fun png(width: Int, height: Int, raw: ByteArray): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        out.write(byteArrayOf(-119, 80, 78, 71, 13, 10, 26, 10))
        val header = java.io.ByteArrayOutputStream().apply {
            write(java.nio.ByteBuffer.allocate(13).putInt(width).putInt(height)
                .put(8).put(0).put(0).put(0).put(0).array())
        }.toByteArray()
        out.write(chunk("IHDR", header))
        out.write(chunk("IDAT", java.util.zip.Deflater().let { deflater ->
            deflater.setInput(raw)
            deflater.finish()
            val buffer = ByteArray(raw.size + 64)
            val size = deflater.deflate(buffer)
            buffer.copyOf(size)
        }))
        out.write(chunk("IEND", ByteArray(0)))
        return out.toByteArray()
    }

    private fun chunk(type: String, data: ByteArray): ByteArray {
        val typeBytes = type.toByteArray(Charsets.US_ASCII)
        val crc = java.util.zip.CRC32()
        crc.update(typeBytes)
        crc.update(data)
        val out = java.io.ByteArrayOutputStream()
        out.write(java.nio.ByteBuffer.allocate(4).putInt(data.size).array())
        out.write(typeBytes)
        out.write(data)
        out.write(java.nio.ByteBuffer.allocate(4).putInt(crc.value.toInt()).array())
        return out.toByteArray()
    }
}
