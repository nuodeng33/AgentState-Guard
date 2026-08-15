package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.LocaleController
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Settings: the language preference (local UI setting only — it never
 * changes authority, security, or evidence state).
 */
@Composable
fun SettingsScreen(
    preference: String,
    onPreferenceChange: (String) -> Unit,
) {
    val options = listOf(
        LocaleController.PREF_SYSTEM to R.string.language_system,
        LocaleController.PREF_EN to R.string.language_en,
        LocaleController.PREF_ZH to R.string.language_zh,
    )
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.more_settings),
            style = MaterialTheme.typography.headlineSmall,
        )
        Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
            Column(modifier = Modifier.padding(Spacing.l)) {
                Text(
                    stringResource(R.string.settings_language),
                    style = MaterialTheme.typography.titleSmall,
                )
                options.forEach { (value, labelRes) ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .fillMaxWidth()
                            .selectable(
                                selected = preference == value,
                                onClick = { onPreferenceChange(value) },
                            )
                            .padding(vertical = Spacing.xs),
                    ) {
                        RadioButton(selected = preference == value, onClick = null)
                        Text(
                            stringResource(labelRes),
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.padding(start = Spacing.s),
                        )
                    }
                }
                Text(
                    stringResource(R.string.language_note),
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
        }
    }
}
