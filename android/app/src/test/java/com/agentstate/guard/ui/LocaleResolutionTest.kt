package com.agentstate.guard.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class LocaleResolutionTest {
    @Test
    fun `chinese system languages map to zh-CN`() {
        assertEquals("zh-CN", LocaleController.resolveLocaleTag("system", "zh-CN"))
        assertEquals("zh-CN", LocaleController.resolveLocaleTag("system", "zh-TW"))
        assertEquals("zh-CN", LocaleController.resolveLocaleTag("system", "zh"))
    }

    @Test
    fun `other system languages fall back to en-US`() {
        assertEquals("en-US", LocaleController.resolveLocaleTag("system", "en-US"))
        assertEquals("en-US", LocaleController.resolveLocaleTag("system", "ja-JP"))
        assertEquals("en-US", LocaleController.resolveLocaleTag("system", ""))
    }

    @Test
    fun `explicit preference always wins`() {
        assertEquals("en-US", LocaleController.resolveLocaleTag("en-US", "zh-CN"))
        assertEquals("zh-CN", LocaleController.resolveLocaleTag("zh-CN", "en-US"))
    }

    @Test
    fun `unknown preference behaves as system`() {
        assertEquals("zh-CN", LocaleController.resolveLocaleTag("fr-FR", "zh-CN"))
        assertEquals("en-US", LocaleController.resolveLocaleTag("fr-FR", "en-US"))
    }
}
