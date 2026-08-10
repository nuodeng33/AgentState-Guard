package com.agentstate.guard.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
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
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor

/** Destination entries hosted under the More tab. */
enum class MoreDestination(val route: String, val labelRes: Int) {
    Checkpoints("more/checkpoints", R.string.more_checkpoints),
    Recovery("more/recovery", R.string.more_recovery),
    AiMonitor("more/ai", R.string.more_ai_monitor),
    Devices("more/devices", R.string.more_devices),
    Settings("more/settings", R.string.more_settings),
}

/** More: compact menu for the secondary surfaces. */
@Composable
fun MoreScreen(onOpen: (MoreDestination) -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.s),
    ) {
        Text(
            stringResource(R.string.nav_more),
            style = MaterialTheme.typography.headlineSmall,
        )
        MoreDestination.entries.forEach { destination ->
            Card(
                colors = CardDefaults.cardColors(containerColor = SurfaceColor),
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onOpen(destination) },
            ) {
                Row(modifier = Modifier.padding(Spacing.l)) {
                    Text(
                        stringResource(destination.labelRes),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
    }
}
