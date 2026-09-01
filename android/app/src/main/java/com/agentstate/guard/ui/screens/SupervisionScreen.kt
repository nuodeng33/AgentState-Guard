package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.components.DataStateHost
import com.agentstate.guard.ui.components.EmptyStateCard
import com.agentstate.guard.ui.components.FreshnessCaption
import com.agentstate.guard.ui.components.SectionHeader
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.SupervisionSessionUi
import com.agentstate.guard.ui.state.SupervisionAgentUi
import com.agentstate.guard.ui.state.SupervisionUiState
import com.agentstate.guard.ui.state.VerifiedActivityUi
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.Surface as SurfaceColor
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Supervision — answers, in order:
 *   1. Is an agent working?            (UNKNOWN without a direct current fact)
 *   2. Is anything blocked / failed?   (session blocked_or_failed_reason)
 *   3. Does it need your intervention? (pending approval with action buttons)
 *   4. What is the current action?     (direct current_* facts only)
 *   5. What was recently confirmed?    (recent verified activities)
 *   6. Sessions at a glance
 *
 * Statuses, reason codes and action references are backend tokens rendered
 * verbatim; the screen never invents current work, never infers liveness
 * from elapsed time, and never treats AI assessment as authority.
 */
@Composable
fun SupervisionScreen(
    state: SupervisionUiState,
    modifier: Modifier = Modifier,
    onApprove: (sessionId: String, actionRef: String) -> Unit = { _, _ -> },
    onReject: (sessionId: String, actionRef: String) -> Unit = { _, _ -> },
    actionInProgressSessionId: String? = null,
    actionResultReasonCode: String? = null,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(Spacing.l)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(
            stringResource(R.string.nav_supervision),
            style = MaterialTheme.typography.headlineSmall,
        )
        FreshnessCaption(
            lastKnown = state.lastKnown,
            observedAt = state.observedAt,
            syncedAtEpochMs = state.syncedAtEpochMs,
        )
        DataStateHost(
            phase = state.phase,
            loadingText = stringResource(R.string.state_loading),
            emptyText = stringResource(R.string.empty_supervision),
            lastKnown = state.lastKnown,
            reasonCode = state.reasonCode,
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.m)) {
                // No backend field proves that an observed agent is currently working.
                SectionHeader(stringResource(R.string.supervision_q_agent_working))
                FactRow(
                    primary = "UNKNOWN",
                    secondary = stringResource(R.string.supervision_no_working_fact),
                )
                if (state.observedAgents.isNotEmpty()) {
                    SectionHeader(stringResource(R.string.supervision_q_agents_observed))
                    state.observedAgents.forEach { agent ->
                        AgentFactCard(agent)
                    }
                }

                // 2. Blocked / failed sessions.
                val blocked = state.sessions.filter { it.blockedOrFailedReason != null }
                if (blocked.isNotEmpty()) {
                    SectionHeader(stringResource(R.string.supervision_q_blocked))
                    blocked.forEach { session ->
                        FactRow(
                            primary = session.sessionId,
                            secondary = session.blockedOrFailedReason,
                        )
                    }
                }

                // 3. Pending approval with a server-issued action_ref: actionable.
                val pending = state.sessions.filter { it.pendingApproval == true && it.actionRef != null }
                if (pending.isNotEmpty()) {
                    SectionHeader(stringResource(R.string.supervision_q_intervention))
                    pending.forEach { session ->
                        PendingApprovalCard(
                            session = session,
                            onApprove = onApprove,
                            onReject = onReject,
                            inProgress = actionInProgressSessionId == session.sessionId,
                            resultReasonCode = if (actionInProgressSessionId == null) {
                                actionResultReasonCode
                            } else {
                                null
                            },
                        )
                    }
                }

                // Current means a direct live backend current_* fact, never latest history.
                SectionHeader(stringResource(R.string.supervision_q_current_action))
                val current = if (state.phase == DataPhase.CONNECTED && !state.lastKnown) {
                    state.sessions.firstOrNull { session ->
                        session.currentTask != null ||
                            session.currentPhase != null ||
                            session.currentAction != null
                    }
                } else {
                    null
                }
                if (current == null) {
                    Text(
                        stringResource(R.string.supervision_no_current_fact),
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary,
                    )
                } else {
                    FactRow(
                        primary = current.sessionId,
                        secondary = listOfNotNull(
                            current.currentTask?.let {
                                stringResource(R.string.supervision_current_task) + ": " + it
                            },
                            current.currentPhase?.let {
                                stringResource(R.string.supervision_current_phase) + ": " + it
                            },
                            current.currentAction?.let {
                                stringResource(R.string.supervision_current_action) + ": " + it
                            },
                        ).joinToString(" · "),
                    )
                }

                // 5. Recently confirmed activity.
                if (state.recentActivities.isNotEmpty()) {
                    SectionHeader(stringResource(R.string.supervision_q_recent_activities))
                    state.recentActivities.forEach { activity ->
                        ActivityRow(activity, sessionContext = null)
                    }
                }

                // 6. Sessions at a glance.
                SectionHeader(stringResource(R.string.supervision_session))
                if (state.sessions.isEmpty()) {
                    Text(
                        stringResource(R.string.state_no_data),
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary,
                    )
                } else {
                    state.sessions.forEach { session ->
                        FactRow(
                            primary = session.sessionId,
                            secondary = listOfNotNull(session.status, session.policyDecision)
                                .filter { it.isNotBlank() }
                                .joinToString(" · "),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AgentFactCard(agent: SupervisionAgentUi) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.m),
            verticalArrangement = Arrangement.spacedBy(Spacing.xs),
        ) {
            Text(agent.identity, style = MaterialTheme.typography.titleSmall)
            Text("agent_ref: ${agent.agentRef ?: "—"}", color = TextSecondary)
            Text("role/lifecycle: ${listOfNotNull(agent.role, agent.lifecycle).joinToString(" · ")}")
            Text("execution_domain_id: ${agent.executionDomainId ?: "—"}")
            Text("workspace: ${agent.workspaceStatus ?: "UNKNOWN"} · ${agent.workspaceId ?: "—"}")
            Text("activity: ${agent.activityObservability ?: "UNKNOWN"} · ${agent.recentActivityCount ?: "UNKNOWN"}")
            Text("supervision: ${agent.supervisionStatus ?: "UNKNOWN"} · ${agent.supervisionSessionId ?: "—"}")
            Text("policy: ${agent.policyDecision ?: "—"} · pending=${agent.pendingApproval ?: "UNKNOWN"}")
            Text("protection: ${agent.protectionState ?: "UNKNOWN"} · ${agent.verificationState ?: "NOT_VERIFIED"}")
            Text("checkpoint: ${agent.latestCheckpointId ?: "—"}")
            agent.latestVerifiedChange?.let { change ->
                Text("latest_verified_change: ${change.changeType} · ${change.eventId ?: "—"}")
            }
            Text("reason_code: ${agent.activityReasonCode ?: "—"}")
            Text("evidence_refs: ${agent.evidenceRefs?.joinToString(", ") ?: "—"}")
        }
    }
}

@Composable
private fun FactRow(primary: String, secondary: String?) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.m),
        ) {
            Text(primary, style = MaterialTheme.typography.bodyMedium)
            if (secondary != null) {
                Text(
                    secondary,
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
        }
    }
}

/** Pending-approval card; actions are enabled only for a server action_ref. */
@Composable
private fun PendingApprovalCard(
    session: SupervisionSessionUi,
    onApprove: (String, String) -> Unit,
    onReject: (String, String) -> Unit,
    inProgress: Boolean,
    resultReasonCode: String?,
) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.m),
            verticalArrangement = Arrangement.spacedBy(Spacing.s),
        ) {
            FactRow(
                primary = session.sessionId,
                secondary = listOfNotNull(
                    session.status,
                    session.policyDecision,
                    if (session.requiresCheckpoint == true) {
                        stringResource(R.string.supervision_checkpoint_required)
                    } else {
                        null
                    },
                ).filter { it.isNotBlank() }.joinToString(" · "),
            )
            session.actionRef?.let { actionRef ->
                Row(horizontalArrangement = Arrangement.spacedBy(Spacing.s)) {
                    OutlinedButton(
                        onClick = { onReject(session.sessionId, actionRef) },
                        enabled = !inProgress,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(stringResource(R.string.reject_action))
                    }
                    Button(
                        onClick = { onApprove(session.sessionId, actionRef) },
                        enabled = !inProgress,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(stringResource(R.string.approve_once_action))
                    }
                }
            }
            when {
                inProgress -> Text(
                    stringResource(R.string.state_loading),
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
                resultReasonCode != null -> FactRow(
                    primary = stringResource(R.string.action_failed),
                    secondary = resultReasonCode,
                )
            }
        }
    }
}

/** One verified-activity row; timestamp/result/reason render verbatim. */
@Composable
private fun ActivityRow(activity: VerifiedActivityUi, sessionContext: String?) {
    Card(colors = CardDefaults.cardColors(containerColor = SurfaceColor)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(Spacing.m),
        ) {
            Text(activity.type, style = MaterialTheme.typography.bodyMedium)
            val detail = listOfNotNull(
                activity.timestamp,
                activity.result,
                activity.reasonCode,
                sessionContext,
            ).filter { it.isNotBlank() }.joinToString(" · ")
            if (detail.isNotBlank()) {
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
        }
    }
}
