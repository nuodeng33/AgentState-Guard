package com.agentstate.guard.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Guards that every user-facing string key exists in both en (default) and
 * zh-rCN resources so a locale switch never falls back to a missing key.
 *
 * Unit-test working directory is the module dir (android/app).
 */
class StringsParityTest {

    private fun keys(path: String): Set<String> {
        val file = File(path)
        assertTrue("missing resource file: $path", file.isFile)
        val doc = DocumentBuilderFactory.newInstance()
            .newDocumentBuilder()
            .parse(file)
        val result = mutableSetOf<String>()
        val nodes = doc.getElementsByTagName("string")
        for (i in 0 until nodes.length) {
            val el = nodes.item(i) as Element
            result += el.getAttribute("name")
        }
        return result
    }

    @Test
    fun `en and zh-rCN string keys are identical`() {
        val en = keys("src/main/res/values/strings.xml")
        val zh = keys("src/main/res/values-zh-rCN/strings.xml")
        assertTrue("en strings.xml must not be empty", en.isNotEmpty())
        assertEquals("keys only in en: ${en - zh}; keys only in zh-rCN: ${zh - en}", en, zh)
    }

    @Test
    fun `machine tokens are not translated in zh-rCN`() {
        val file = File("src/main/res/values-zh-rCN/strings.xml")
        // A translated string value must never replace a raw token wholesale:
        // spot-check that no zh value is exactly a bare translation of a token.
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
        val nodes = doc.getElementsByTagName("string")
        for (i in 0 until nodes.length) {
            val el = nodes.item(i) as Element
            val value = el.textContent.trim()
            assertTrue(
                "string '${el.getAttribute("name")}' looks like a translated machine token",
                value !in setOf("未知", "审核", "阻止", "允许")
            )
        }
    }
}
