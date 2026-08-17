package com.agentstate.guard.qr

import java.io.File

/**
 * JVM fixture renderer: loads the committed module matrices produced by
 * scripts/generate_qr_fixtures.py, adds the spec quiet zone, and scales each
 * module to a pixel block — a real encoder->decoder round trip through the
 * production engine, without binaries or an emulator.
 */
object QrFixture {
    private const val QUIET_ZONE_MODULES = 4
    private const val PIXELS_PER_MODULE = 4

    data class Frame(val payloadText: String, val grid: QrPixelGrid)

    /** Loads one named fixture matrix; the payload comment must match exactly. */
    fun render(name: String): Frame {
        val file = File("src/test/resources/qr/$name.matrix.txt")
        check(file.isFile) { "missing fixture $name — run scripts/generate_qr_fixtures.py" }
        val lines = file.readLines()
        val payload = lines.first().removePrefix("# payload: ").trim()
        val matrix = lines.drop(1).map { row -> row.map { it == '1' } }
        val modules = matrix.size
        val totalModules = modules + 2 * QUIET_ZONE_MODULES
        val scale = PIXELS_PER_MODULE
        val size = totalModules * scale
        // Luminance: paper white background, near-black modules.
        val samples = ByteArray(size * size) { 0xE8.toByte() }
        matrix.forEachIndexed { rowIndex, row ->
            row.forEachIndexed { columnIndex, dark ->
                if (dark) {
                    val startRow = (rowIndex + QUIET_ZONE_MODULES) * scale
                    val startCol = (columnIndex + QUIET_ZONE_MODULES) * scale
                    for (r in startRow until startRow + scale) {
                        for (c in startCol until startCol + scale) {
                            samples[r * size + c] = 0x10
                        }
                    }
                }
            }
        }
        return Frame(payload, QrPixelGrid(size, size, samples))
    }

    /** Uniform bright frame with no symbol present. */
    fun blank(): QrPixelGrid {
        val size = 64 * PIXELS_PER_MODULE
        return QrPixelGrid(size, size, ByteArray(size * size) { 0xE0.toByte() })
    }

    /** Uniform dark frame with no symbol present (fails closed, not a QR). */
    fun dark(): QrPixelGrid {
        val size = 64 * PIXELS_PER_MODULE
        return QrPixelGrid(size, size, ByteArray(size * size) { 0x08 })
    }
}
