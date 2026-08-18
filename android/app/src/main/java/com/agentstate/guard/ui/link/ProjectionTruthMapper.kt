package com.agentstate.guard.ui.link

import com.agentstate.guard.network.DeviceLinkSnapshot
import com.agentstate.guard.ui.state.ChangeUi
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.state.SupervisionActionUiResult
import com.agentstate.guard.ui.state.SupervisionSessionUi
import com.agentstate.guard.ui.state.VerifiedActivityUi
import org.json.JSONArray
import org.json.JSONObject

/**
 * The single Android boundary that translates bounded backend facts into UI
 * state. Transport reachability is evaluated first; an online transport then
 * preserves the status of each individual projection instead of manufacturing
 * a healthy aggregate.
 */
internal object ProjectionTruthMapper {
    fun phase(online: Boolean, projection: JSONObject?): DataPhase {
        if (!online) return DataPhase.UNREACHABLE
        return when (projection.stringFact("status")?.uppercase(java.util.Locale.ROOT)) {
            "AVAILABLE", "ENABLED", "ACTIVE" -> DataPhase.CONNECTED
            "DEGRADED" -> DataPhase.DEGRADED
            "EMPTY", "NOT_FOUND", "DISABLED" -> DataPhase.EMPTY
            "UNREACHABLE" -> DataPhase.UNREACHABLE
            "ERROR" -> DataPhase.ERROR
            "UNKNOWN", null -> DataPhase.UNKNOWN
            else -> DataPhase.UNKNOWN
        }
    }

    /** Offline is last-known only when at least one cached projection exists. */
    fun isLastKnown(snapshot: DeviceLinkSnapshot): Boolean = !snapshot.online && listOf(
        snapshot.status,
        snapshot.environment,
        snapshot.agents,
        snapshot.supervision,
        snapshot.changes,
        snapshot.checkpoints,
        snapshot.recovery,
        snapshot.aiAdvisory,
    ).any { it != null }

    /** A present projection with no usable status remains explicitly UNKNOWN. */
    fun projectionStatus(projection: JSONObject?): String? = projection?.let {
        it.stringFact("status") ?: "UNKNOWN"
    }

    fun aiConfigured(projection: JSONObject?): Boolean? = when (
        projection.stringFact("status")
    ) {
        "AVAILABLE" -> true
        "UNAVAILABLE" -> false
        else -> null
    }

    fun pendingCount(supervision: JSONObject): Int? {
        val items = supervision.arrayFact("items") ?: return null
        var pending = 0
        for (index in 0 until items.length()) {
            val item = items.optJSONObject(index) ?: return null
            when (item.booleanFact("pending_approval")) {
                true -> pending += 1
                false -> Unit
                null -> return null
            }
        }
        return pending
    }

    fun change(item: JSONObject): ChangeUi {
        val eventId = item.stringFact("event_id")
        val subject = item.stringFact("subject")
        val affectedObjects = item.stringListFact("affected_objects")
        return ChangeUi(
            file = subject ?: affectedObjects?.firstOrNull() ?: eventId ?: "UNKNOWN",
            changeType = item.stringFact("type") ?: "UNKNOWN",
            whenText = item.stringFact("timestamp"),
            eventId = eventId,
            actor = item.stringFact("actor"),
            subject = subject,
            result = item.stringFact("result"),
            reasonCode = item.stringFact("reason_code"),
            checkpointId = item.stringFact("checkpoint_id"),
            affectedObjects = affectedObjects,
            verificationSummary = item.stringFact("verification_summary"),
            executionDomainId = item.stringFact("execution_domain_id"),
            attribution = item.stringFact("attribution"),
            changeKind = item.stringFact("change_kind"),
            coverageBefore = item.stringFact("coverage_before"),
            coverageAfter = item.stringFact("coverage_after"),
            recoveryDisposition = item.stringFact("recovery_disposition"),
            workspaceId = item.stringFact("workspace_id"),
            evidenceRefs = item.stringListFact("evidence_refs"),
        )
    }

    fun supervisionSession(item: JSONObject): SupervisionSessionUi = SupervisionSessionUi(
        sessionId = item.stringFact("supervision_session_id") ?: "UNKNOWN",
        status = item.stringFact("status") ?: "UNKNOWN",
        policyDecision = item.stringFact("policy_decision"),
        pendingApproval = item.booleanFact("pending_approval"),
        blockedOrFailedReason = item.stringFact("blocked_or_failed_reason"),
        actionRef = item.stringFact("action_ref"),
        requiresCheckpoint = item.booleanFact("requires_checkpoint"),
        latestVerifiedActivity = item.objectFact("latest_verified_activity")
            ?.let(::verifiedActivity),
        currentTask = item.stringFact("current_task"),
        currentPhase = item.stringFact("current_phase"),
        currentAction = item.stringFact("current_action"),
        observedAt = item.stringFact("observed_at"),
    )

    fun evidence(dto: JSONObject, requestedEventId: String): EvidenceUiState {
        val detail = dto.objectFact("sanitized_detail")
        val verification = dto.stringFact("verification_summary")
            ?: detail.stringFact("verification")
        return EvidenceUiState(
            phase = phase(online = true, projection = dto),
            status = dto.stringFact("status"),
            reasonCode = dto.stringFact("reason_code"),
            eventId = dto.stringFact("event_id") ?: requestedEventId,
            eventType = dto.stringFact("event_type"),
            observedAt = dto.stringFact("observed_at"),
            recordedAt = dto.stringFact("recorded_at"),
            source = dto.stringFact("source"),
            subject = dto.stringFact("subject"),
            result = dto.stringFact("result"),
            verificationSummary = verification?.let(::listOf),
            affectedObjects = detail?.stringListFact("affected_objects"),
            checkpointId = dto.stringFact("checkpoint_id"),
            changeId = dto.stringFact("change_id"),
            chainRef = dto.stringFact("chain_ref"),
            executionDomainId = dto.stringFact("execution_domain_id"),
            attribution = detail.stringFact("attribution"),
            changeKind = detail.stringFact("change_kind"),
            coverageBefore = detail.stringFact("coverage_before"),
            coverageAfter = detail.stringFact("coverage_after"),
            recoveryDisposition = detail.stringFact("recovery_disposition"),
            workspaceId = detail.stringFact("workspace_id"),
            relatedEvidenceRefs = dto.stringListFact("related_evidence_refs"),
        )
    }

    fun evidenceHttpFailure(
        status: Int,
        reasonCode: String,
        requestedEventId: String,
    ): EvidenceUiState = if (status == 404 && reasonCode == "EVIDENCE_EVENT_NOT_FOUND") {
        EvidenceUiState(
            phase = DataPhase.EMPTY,
            status = "NOT_FOUND",
            reasonCode = reasonCode,
            eventId = requestedEventId,
        )
    } else {
        EvidenceUiState(
            phase = DataPhase.ERROR,
            status = "UNAVAILABLE",
            reasonCode = reasonCode,
            eventId = requestedEventId,
        )
    }

    fun supervisionAction(
        result: JSONObject,
        expectedAction: String,
        expectedSessionId: String,
    ): SupervisionActionUiResult {
        val consumed = result.booleanFact("consumed")
        val status = result.stringFact("status")
        val reason = result.stringFact("reason_code")
        val action = result.stringFact("action")
        val valid = result.stringFact("schema_version") == "r4-p8-action-1" &&
            expectedAction in setOf("APPROVE_ONCE", "REJECT") &&
            action == expectedAction &&
            result.stringFact("supervision_session_id") == expectedSessionId &&
            status != null && reason != null && consumed != null
        return SupervisionActionUiResult(
            succeeded = valid && consumed == true,
            status = status,
            reasonCode = if (valid) reason else "SUPERVISION_ACTION_RESPONSE_INVALID",
        )
    }

    fun verifiedActivity(value: JSONObject): VerifiedActivityUi = VerifiedActivityUi(
        eventId = value.stringFact("event_id") ?: "UNKNOWN",
        type = value.stringFact("type") ?: "UNKNOWN",
        result = value.stringFact("result"),
        reasonCode = value.stringFact("reason_code"),
        timestamp = value.stringFact("timestamp"),
        checkpointId = value.stringFact("checkpoint_id"),
    )

    private fun JSONObject?.stringFact(key: String): String? {
        if (this == null || !has(key) || isNull(key)) return null
        return (opt(key) as? String)?.takeIf { it.isNotBlank() }
    }

    private fun JSONObject.booleanFact(key: String): Boolean? {
        if (!has(key) || isNull(key)) return null
        return opt(key) as? Boolean
    }

    private fun JSONObject.objectFact(key: String): JSONObject? {
        if (!has(key) || isNull(key)) return null
        return optJSONObject(key)
    }

    private fun JSONObject.arrayFact(key: String): JSONArray? {
        if (!has(key) || isNull(key)) return null
        return optJSONArray(key)
    }

    private fun JSONObject.stringListFact(key: String): List<String>? {
        val array = arrayFact(key) ?: return null
        val values = ArrayList<String>(array.length())
        for (index in 0 until array.length()) {
            val value = array.opt(index) as? String ?: return null
            if (value.isBlank()) return null
            values += value
        }
        return values
    }
}
