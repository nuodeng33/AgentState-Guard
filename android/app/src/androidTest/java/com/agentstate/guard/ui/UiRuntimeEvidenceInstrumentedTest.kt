package com.agentstate.guard.ui

import android.content.Intent
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.agentstate.guard.MainActivity
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.screens.EnvironmentScreen
import com.agentstate.guard.ui.screens.SasScreen
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentItemUi
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.theme.AgentStateTheme
import java.util.concurrent.atomic.AtomicInteger
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UiRuntimeEvidenceInstrumentedTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext
    private val activities = mutableListOf<MainActivity>()
    private lateinit var priorLanguagePreference: String

    @Before
    fun savePriorLanguagePreference() {
        priorLanguagePreference = LocaleController.loadPreference(context)
    }

    @After
    fun restorePriorPreferenceAndFinishActivities() {
        activities.toList().forEach(::finishActivity)
        LocaleController.savePreference(context, priorLanguagePreference)
    }

    @Test
    fun mainActivity_hasHonestNoopTabsAndNoPreviewFixtures() {
        LocaleController.savePreference(context, LocaleController.PREF_EN)
        val activity = launchFreshActivity()
        try {
            waitForText("Not connected to a computer")
            assertTextAbsent("DESKTOP-DEMO")
            listOf("Home", "Environment", "Changes", "Supervision", "More").forEach(::waitForText)

            clickText("Environment")
            waitForText("No environment data")
            listOf("Claude Code", "Node.js", "Python", "Docker", "Tailscale").forEach(::assertTextAbsent)
            clickText("Changes")
            waitForText("No changes detected")
            clickText("Supervision")
            waitForText("No supervision sessions")
            clickText("More")
            listOf("Checkpoints", "Recovery", "AI Monitor", "Devices", "Settings").forEach(::waitForText)
        } finally {
            finishActivity(activity)
        }
    }

    @Test
    fun environmentScreen_exposesLoadingEmptyAndConnectedItemData() {
        LocaleController.savePreference(context, LocaleController.PREF_EN)
        val activity = launchFreshActivity()
        try {
            render(activity) {
                EnvironmentScreen(EnvironmentUiState(phase = DataPhase.LOADING))
            }
            waitForText("Loading…")

            render(activity) {
                EnvironmentScreen(EnvironmentUiState(phase = DataPhase.EMPTY))
            }
            waitForText("No environment data")

            render(activity) {
                EnvironmentScreen(
                    EnvironmentUiState(
                        phase = DataPhase.CONNECTED,
                        items = listOf(EnvironmentItemUi("Runtime Probe", "v1.2.3", "UNREACHABLE")),
                    ),
                )
            }
            listOf("Runtime Probe", "v1.2.3", "UNREACHABLE").forEach(::waitForText)
        } finally {
            finishActivity(activity)
        }
    }

    @Test
    fun sasScreen_invokesOnlyTheExplicitConfirmAndRejectCallbacks() {
        LocaleController.savePreference(context, LocaleController.PREF_EN)
        val activity = launchFreshActivity()
        val confirmed = AtomicInteger(0)
        val rejected = AtomicInteger(0)
        try {
            render(activity) {
                SasScreen(
                    state = PairingUiState(phase = PairingPhase.SAS_PENDING, sasCode = "123456"),
                    secondsLeft = null,
                    onConfirm = { confirmed.incrementAndGet() },
                    onReject = { rejected.incrementAndGet() },
                )
            }
            listOf("123 456", "Codes match", "Codes do not match — cancel").forEach(::waitForText)
            assertEquals(0, confirmed.get())
            assertEquals(0, rejected.get())

            clickText("Codes match")
            waitForCondition("confirm callback exactly once without reject") {
                confirmed.get() == 1 && rejected.get() == 0
            }
            assertEquals(1, confirmed.get())
            assertEquals(0, rejected.get())

            clickText("Codes do not match — cancel")
            waitForCondition("reject callback exactly once without another confirm") {
                confirmed.get() == 1 && rejected.get() == 1
            }
            assertEquals(1, confirmed.get())
            assertEquals(1, rejected.get())
        } finally {
            finishActivity(activity)
        }
    }

    @Test
    fun freshActivities_applyExplicitEnglishAndChineseLocales() {
        LocaleController.savePreference(context, LocaleController.PREF_EN)
        val englishActivity = launchFreshActivity()
        try {
            assertEquals("en", englishActivity.resources.configuration.locales[0].language)
            waitForText("Home")
            waitForText("More")
        } finally {
            finishActivity(englishActivity)
        }

        LocaleController.savePreference(context, LocaleController.PREF_ZH)
        val chineseActivity = launchFreshActivity()
        try {
            assertEquals("zh", chineseActivity.resources.configuration.locales[0].language)
            waitForText("首页")
            waitForText("更多")
        } finally {
            finishActivity(chineseActivity)
        }
    }

    private fun launchFreshActivity(): MainActivity {
        val activity = instrumentation.startActivitySync(
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        ) as MainActivity
        activities += activity
        instrumentation.waitForIdleSync()
        return activity
    }

    private fun finishActivity(activity: MainActivity) {
        if (activities.remove(activity)) {
            instrumentation.runOnMainSync { activity.finish() }
            instrumentation.waitForIdleSync()
        }
    }

    private fun render(activity: MainActivity, content: @Composable () -> Unit) {
        instrumentation.runOnMainSync {
            activity.setContent {
                AgentStateTheme { content() }
            }
        }
        instrumentation.waitForIdleSync()
    }

    private fun waitForText(text: String): AccessibilityNodeInfo {
        val deadline = SystemClock.uptimeMillis() + ACCESSIBILITY_TIMEOUT_MS
        do {
            findExactText(text)?.let { return it }
            SystemClock.sleep(ACCESSIBILITY_POLL_INTERVAL_MS)
        } while (SystemClock.uptimeMillis() < deadline)
        throw AssertionError("Timed out waiting for exact accessibility text: $text")
    }

    private fun assertTextAbsent(text: String) {
        assertNull("Unexpected exact accessibility text: $text", findExactText(text))
    }

    private fun clickText(text: String) {
        var node = waitForText(text)
        while (!node.isClickable) {
            node = node.parent
                ?: throw AssertionError("No clickable accessibility ancestor for exact text: $text")
        }
        assertTrue(
            "Accessibility click failed for exact text: $text",
            node.performAction(AccessibilityNodeInfo.ACTION_CLICK),
        )
        instrumentation.waitForIdleSync()
    }

    private fun waitForCondition(description: String, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + ACCESSIBILITY_TIMEOUT_MS
        do {
            if (condition()) return
            instrumentation.waitForIdleSync()
            SystemClock.sleep(ACCESSIBILITY_POLL_INTERVAL_MS)
        } while (SystemClock.uptimeMillis() < deadline)
        throw AssertionError("Timed out waiting for condition: $description")
    }

    private fun findExactText(text: String): AccessibilityNodeInfo? =
        instrumentation.uiAutomation.rootInActiveWindow?.let { root -> findExactText(root, text) }

    private fun findExactText(node: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        if (node.text?.toString() == text || node.contentDescription?.toString() == text) return node
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            findExactText(child, text)?.let { return it }
        }
        return null
    }

    private companion object {
        const val ACCESSIBILITY_TIMEOUT_MS = 5_000L
        const val ACCESSIBILITY_POLL_INTERVAL_MS = 50L
    }
}
