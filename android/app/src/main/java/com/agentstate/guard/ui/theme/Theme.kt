package com.agentstate.guard.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

/**
 * AgentState Guard product theme. The product is a dark security operations
 * console in every context; the semantic status tones live in Color.kt and
 * never collapse into each other. Unknown/unreachable stay slate and are
 * never rendered as safe.
 */
private val ConsoleColorScheme = darkColorScheme(
    primary = Primary,
    secondary = Secondary,
    background = Background,
    surface = Surface,
    surfaceVariant = SurfaceMuted,
    onPrimary = TextPrimary,
    onSecondary = TextPrimary,
    onBackground = TextPrimary,
    onSurface = TextPrimary,
    onSurfaceVariant = TextSecondary,
    outline = BorderStrong,
    error = Danger,
    onError = TextPrimary,
)

@Composable
fun AgentStateTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = ConsoleColorScheme,
        typography = ConsoleTypography,
        shapes = ConsoleShapes,
        content = content,
    )
}
