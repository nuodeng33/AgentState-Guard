package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.SupervisionActionUiResult
import com.agentstate.guard.ui.state.SupervisionSessionUi
import com.agentstate.guard.ui.state.SupervisionUiState
import com.agentstate.guard.ui.theme.Spacing

/** Only fresh, server-issued pending intents can be confirmed; no apply/restore controls. */
fun canSubmitSupervision(state: SupervisionUiState, item: SupervisionSessionUi): Boolean =
    state.phase == DataPhase.CONNECTED && !state.lastKnown && item.pendingApproval &&
        item.status == "AWAITING_APPROVAL" && item.policyDecision == "REVIEW" &&
        item.actionRef?.matches(Regex("^[0-9a-f]{64}$")) == true

private data class Decision(val sessionId: String, val actionRef: String, val approve: Boolean)

@Composable
fun SupervisionScreen(
    state: SupervisionUiState,
    busy: Boolean = false,
    result: SupervisionActionUiResult? = null,
    onDecision: (String, String, Boolean) -> Unit = { _, _, _ -> },
    onEvidence: (String) -> Unit = {},
    onRefresh: () -> Unit = {},
) {
    var decision by remember { mutableStateOf<Decision?>(null) }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(stringResource(R.string.nav_supervision), style = MaterialTheme.typography.headlineSmall)
        Text(state.phase.name)
        if (state.lastKnown) Text(stringResource(R.string.connection_last_known))
        state.reasonCode?.let { Text(it) }
        state.observedAt?.let { Text(stringResource(R.string.connection_observed_at, it)) }
        Text("${stringResource(R.string.home_pending_supervision)}: ${state.pendingCount}")
        result?.let {
            Text(if (it.succeeded) it.status ?: "" else stringResource(R.string.action_failed))
            it.reasonCode?.let { code -> Text(code) }
        }
        state.sessions.forEach { item ->
            Card { Column(Modifier.padding(Spacing.m), verticalArrangement = Arrangement.spacedBy(Spacing.s)) {
                Text(item.sessionId); Text(item.status)
                item.policyDecision?.let { Text(it) }
                item.blockedOrFailedReason?.let { Text(it) }
                if (item.requiresCheckpoint) Text(stringResource(R.string.supervision_checkpoint_required))
                val allowed = !busy && canSubmitSupervision(state, item)
                OutlinedButton(enabled = allowed, onClick = { decision = Decision(item.sessionId, item.actionRef!!, true) }) {
                    Text(stringResource(R.string.approve_once_action))
                }
                OutlinedButton(enabled = allowed, onClick = { decision = Decision(item.sessionId, item.actionRef!!, false) }) {
                    Text(stringResource(R.string.reject_action))
                }
                item.latestVerifiedActivity?.eventId?.let { id ->
                    OutlinedButton(enabled = !busy, onClick = { onEvidence(id) }) { Text(stringResource(R.string.evidence_title)) }
                }
            } }
        }
        state.recentActivities.forEach { activity ->
            Text("${activity.type} · ${activity.result ?: ""}")
            OutlinedButton(enabled = !busy, onClick = { onEvidence(activity.eventId) }) { Text(activity.eventId) }
        }
        OutlinedButton(enabled = !busy, onClick = onRefresh) { Text(stringResource(R.string.action_refresh)) }
    }
    decision?.let { intent ->
        val stillCurrent = state.sessions.any { it.sessionId == intent.sessionId && it.actionRef == intent.actionRef && canSubmitSupervision(state, it) }
        AlertDialog(
            onDismissRequest = { if (!busy) decision = null },
            title = { Text(stringResource(if (intent.approve) R.string.approve_once_action else R.string.reject_action)) },
            text = { Text(intent.sessionId) },
            confirmButton = { TextButton(enabled = !busy && stillCurrent, onClick = {
                decision = null; onDecision(intent.sessionId, intent.actionRef, intent.approve)
            }) { Text(stringResource(R.string.action_confirm)) } },
            dismissButton = { TextButton(enabled = !busy, onClick = { decision = null }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }
}
