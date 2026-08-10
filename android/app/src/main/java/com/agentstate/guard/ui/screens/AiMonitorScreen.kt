package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.state.AiMonitorUiState
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * AI Monitor. When nothing is configured the page says so; it never shows
 * canned analysis as if it were real.
 */
@Composable
fun AiMonitorScreen(state: AiMonitorUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.more_ai_monitor),
            style = MaterialTheme.typography.headlineSmall,
        )
        if (!state.configured) {
            EmptyStateCard(
                title = stringResource(R.string.ai_not_configured),
                detail = stringResource(R.string.ai_not_configured_detail),
            )
        } else {
            Text(
                state.summary ?: stringResource(R.string.state_no_data),
                style = MaterialTheme.typography.bodyMedium,
                color = TextSecondary,
            )
        }
    }
}
