package com.agentstate.guard.ui

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

/**
 * Language preference for the app UI: follow-system (default), en-US, or
 * zh-CN. A local display preference only — it never affects authority,
 * security, protocol, or evidence semantics, and machine tokens are never
 * translated (they are data, rendered verbatim).
 */
object LocaleController {
    const val PREF_SYSTEM = "system"
    const val PREF_EN = "en-US"
    const val PREF_ZH = "zh-CN"

    private const val PREFS_NAME = "asg_ui_prefs"
    private const val KEY_LANGUAGE = "asg.ui.language"

    fun loadPreference(context: Context): String {
        val raw = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_LANGUAGE, PREF_SYSTEM)
        return when (raw) {
            PREF_EN, PREF_ZH -> raw
            else -> PREF_SYSTEM
        }
    }

    fun savePreference(context: Context, preference: String) {
        val normalized = when (preference) {
            PREF_EN, PREF_ZH -> preference
            else -> PREF_SYSTEM
        }
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_LANGUAGE, normalized)
            .apply()
    }

    /**
     * Follow-system resolution: any Chinese system language maps to zh-CN,
     * everything else falls back to en-US. Explicit preference always wins.
     * Pure and JVM-unit-testable.
     */
    fun resolveLocaleTag(preference: String, systemLanguage: String): String = when {
        preference == PREF_EN || preference == PREF_ZH -> preference
        systemLanguage.lowercase().startsWith("zh") -> PREF_ZH
        else -> PREF_EN
    }

    /**
     * Wrap the base context with the explicit locale when the user chose one;
     * follow-system leaves the configuration untouched.
     */
    fun wrap(base: Context): Context {
        val preference = loadPreference(base)
        if (preference == PREF_SYSTEM) return base
        val locale = Locale.forLanguageTag(preference)
        val config = Configuration(base.resources.configuration)
        config.setLocale(locale)
        config.setLayoutDirection(locale)
        return base.createConfigurationContext(config)
    }
}
