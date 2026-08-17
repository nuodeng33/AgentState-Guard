package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.components.StatusBadge
import com.agentstate.guard.ui.state.AiAdvisoryUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary
import com.agentstate.guard.ui.theme.toneForMachineState

/**
 * Contextual AI advisory card — bounded, read-only context for Home /
 * Environment / Supervision. AI output is advice only; it never becomes an
 * authority signal and the desktop keeps ownership of every decision. When
 * no advisory exists the card silently drops the content (nothing renders).
 */
@Composable
fun AiAdvisoryCard(
    state: AiAdvisoryUiState,
    modifier: Modifier = Modifier,
) {
    val severity = state.severity
    val summary = state.summary ?: return
    if (severity.isNullOrBlank() && summary.isBlank()) return

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = SurfaceColor),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.l),
            verticalArrangement = Arrangement.spacedBy(Spacing.s),
        ) {
            Text(
                stringResource(R.string.ai_advisory_card_title),
                style = MaterialTheme.typography.titleSmall,
            )
            if (state.phase != DataPhase.CONNECTED) {
                StatusBadge(
                    label = state.phase.name,
                    tone = toneForMachineState(state.phase.name),
                )
            }
            FreshnessCaption(
                lastKnown = state.lastKnown,
                observedAt = state.observedAt,
                syncedAtEpochMs = state.syncedAtEpochMs,
            )
            if (!severity.isNullOrBlank()) {
                StatusBadge(
                    label = severity,
                    tone = toneForMachineState(severity),
                )
            }
            Text(
                summary,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                stringResource(R.string.ai_advisory_note),
                style = MaterialTheme.typography.labelSmall,
                color = TextSecondary,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(Spacing.s),
            ) {
                state.analyzedAt?.let {
                    Text(
                        stringResource(R.string.ai_advisory_analyzed_at) + ": " + it,
                        style = MaterialTheme.typography.labelSmall,
                        color = TextSecondary,
                    )
                }
            }
        }
    }
}
