package com.agentstate.guard.ui.screens

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.sp
import com.agentstate.guard.R
import com.agentstate.guard.ui.link.PairingUiState
import com.agentstate.guard.ui.link.PairingPhase
import com.agentstate.guard.ui.theme.ConsoleMono
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.SurfaceElevated
import com.agentstate.guard.ui.theme.TextSecondary

/** Group a six-digit SAS for readability; anything else renders verbatim. */
fun formatSasCode(code: String): String =
    if (code.length == 6) "${code.substring(0, 3)} ${code.substring(3)}" else code

/**
 * SAS confirmation. The code, desktop name, phase, and expiry all come from
 * the adapter; the screen never generates a code and never auto-confirms.
 */
@Composable
fun SasScreen(
    state: PairingUiState,
    secondsLeft: Long?,
    onConfirm: () -> Unit,
    onReject: () -> Unit,
    onCancel: () -> Unit = onReject,
    busy: Boolean = false,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.sas_title),
            style = MaterialTheme.typography.headlineSmall,
        )
        Text(
            stringResource(R.string.sas_prompt),
            style = MaterialTheme.typography.bodyMedium,
            color = TextSecondary,
        )
        Card(colors = CardDefaults.cardColors(containerColor = SurfaceElevated)) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(Spacing.xl),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    text = state.sasCode?.takeIf { it.matches(Regex("^[0-9]{3} [0-9]{3}$")) } ?: "",
                    style = ConsoleMono.copy(
                        fontSize = 30.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 4.sp,
                    ),
                    textAlign = TextAlign.Center,
                )
            }
        }
        state.desktopName?.let { name ->
            Text(
                "${stringResource(R.string.sas_desktop)}: $name",
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }
        Text(
            "${stringResource(R.string.sas_phase)}: ${state.phase.name}",
            style = MaterialTheme.typography.bodySmall,
            color = TextSecondary,
        )
        secondsLeft?.let { seconds ->
            Text(
                stringResource(R.string.sas_expires, seconds),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }
        state.reasonCode?.let { Text(it) }
        Button(onClick = onConfirm, enabled = !busy && state.phase == PairingPhase.SAS_PENDING &&
            state.sasCode?.matches(Regex("^[0-9]{3} [0-9]{3}$")) == true && (secondsLeft ?: 0) > 0,
            modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.sas_confirm))
        }
        OutlinedButton(onClick = onReject, enabled = !busy && state.phase == PairingPhase.SAS_PENDING, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.sas_reject))
        }
        OutlinedButton(onClick = onCancel, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_cancel))
        }
    }
}
