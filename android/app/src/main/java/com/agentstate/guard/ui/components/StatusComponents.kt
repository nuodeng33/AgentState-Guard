package com.agentstate.guard.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.StatusTone
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Status badge: the label is a verbatim machine token; tone only supports it.
 */
@Composable
fun StatusBadge(label: String, tone: StatusTone, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = tone.background,
        border = BorderStroke(1.dp, tone.border),
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            text = label,
            color = tone.foreground,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(horizontal = Spacing.s, vertical = Spacing.xs),
        )
    }
}

/** Consistent section heading. */
@Composable
fun SectionHeader(title: String, modifier: Modifier = Modifier) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleSmall,
        color = TextSecondary,
        modifier = modifier.padding(vertical = Spacing.s),
    )
}

/** Honest empty state: the projection genuinely has no records. */
@Composable
fun EmptyStateCard(title: String, detail: String? = null, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = SurfaceColor),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.l),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            if (detail != null) {
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = Spacing.xs),
                )
            }
        }
    }
}

/**
 * Uniform host for LOADING / no-data phases. LOADING and the no-data phases
 * render explicit placeholders — never fabricated content.
 */
@Composable
fun DataStateHost(
    phase: DataPhase,
    loadingText: String,
    emptyText: String,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    when (phase) {
        DataPhase.LOADING -> Row(modifier.padding(Spacing.l)) {
            Text(loadingText, style = MaterialTheme.typography.bodySmall, color = TextSecondary)
        }
        DataPhase.EMPTY, DataPhase.UNREACHABLE, DataPhase.DEGRADED, DataPhase.ERROR ->
            EmptyStateCard(title = emptyText, modifier = modifier)
        DataPhase.CONNECTED -> content()
    }
}
