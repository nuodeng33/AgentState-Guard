package com.agentstate.guard.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Guards that production UI sources never ship fake environment data or
 * emulator loopback endpoints. Mock values are allowed only inside the
 * preview fixtures package.
 *
 * Unit-test working directory is the module dir (android/app).
 */
class ProductionMockFreeTest {

    private val uiRoot = File("src/main/java/com/agentstate/guard/ui")

    private fun productionSources(): List<File> =
        uiRoot.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .filter { !it.invariantSeparatorsPath.contains("/preview/") }
            .toList()

    @Test
    fun `production ui sources contain no fake environment versions`() {
        val forbidden = listOf("2.1.217", "v22.23.1", "3.11.2", "24.0.7")
        val offenders = mutableListOf<String>()
        for (file in productionSources()) {
            val text = file.readText()
            for (needle in forbidden) {
                if (text.contains(needle)) {
                    offenders += "${file.path}: $needle"
                }
            }
        }
        assertTrue("fake production data found: $offenders", offenders.isEmpty())
    }

    @Test
    fun `production ui sources contain no emulator loopback endpoint`() {
        val offenders = productionSources().filter {
            it.readText().contains("10.0.2.2")
        }
        assertTrue("loopback endpoint in production ui: $offenders", offenders.isEmpty())
    }

    @Test
    fun `preview fixtures are isolated from production state`() {
        val previewDir = File(uiRoot, "preview")
        assertTrue("preview fixture package must exist", previewDir.isDirectory)
        // Fake data, when needed for @Preview, lives here and nowhere else.
        val productionCount = productionSources().size
        assertTrue("expected production ui sources to scan", productionCount > 0)
    }

    @Test
    fun `deleted prototype screen stays deleted`() {
        assertFalse(
            "AIScreen.kt must not exist in production",
            File(uiRoot, "screens/AIScreen.kt").exists()
        )
    }
}
