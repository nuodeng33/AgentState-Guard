package com.agentstate.guard.ui.link

import com.agentstate.guard.network.DeviceLinkSnapshot
import com.agentstate.guard.ui.state.DataPhase
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProjectionTruthMapperTest {
    @Test
    fun `online transport preserves each backend projection phase`() {
        val expected = mapOf(
            "AVAILABLE" to DataPhase.CONNECTED,
            "DEGRADED" to DataPhase.DEGRADED,
            "EMPTY" to DataPhase.EMPTY,
            "NOT_FOUND" to DataPhase.EMPTY,
            "UNKNOWN" to DataPhase.UNKNOWN,
            "UNREACHABLE" to DataPhase.UNREACHABLE,
        )

        expected.forEach { (status, phase) ->
            assertEquals(
                status,
                phase,
                ProjectionTruthMapper.phase(
                    online = true,
                    projection = JSONObject().put("status", status),
                ),
            )
        }
        assertEquals(DataPhase.UNKNOWN, ProjectionTruthMapper.phase(true, JSONObject()))
        assertEquals(DataPhase.UNKNOWN, ProjectionTruthMapper.phase(true, null))
        assertEquals(
            DataPhase.CONNECTED,
            ProjectionTruthMapper.phase(
                online = true,
                projection = JSONObject().put("status", "active"),
            ),
        )
        assertEquals(
            DataPhase.UNREACHABLE,
            ProjectionTruthMapper.phase(
                online = false,
                projection = JSONObject().put("status", "AVAILABLE"),
            ),
        )
    }

    @Test
    fun `initial offline is not mislabeled last-known but cached offline is`() {
        assertFalse(ProjectionTruthMapper.isLastKnown(snapshot()))
        assertTrue(
            ProjectionTruthMapper.isLastKnown(
                snapshot(status = JSONObject().put("status", "ENABLED")),
            ),
        )
    }

    @Test
    fun `missing supervision booleans and aggregate remain unknown`() {
        val missingFacts = JSONObject(
            """{"items":[{"supervision_session_id":"s-1","status":"PENDING"}]}""",
        )
        val mapped = ProjectionTruthMapper.supervisionSession(
            missingFacts.getJSONArray("items").getJSONObject(0),
        )

        assertNull(mapped.pendingApproval)
        assertNull(mapped.requiresCheckpoint)
        assertNull(ProjectionTruthMapper.pendingCount(missingFacts))
        assertEquals(0, ProjectionTruthMapper.pendingCount(JSONObject("""{"items":[]}""")))
        val wrongTypes = JSONObject(
            """{"items":[{"pending_approval":"false","requires_checkpoint":0}]}""",
        )
        val wrongMapped = ProjectionTruthMapper.supervisionSession(
            wrongTypes.getJSONArray("items").getJSONObject(0),
        )
        assertNull(wrongMapped.pendingApproval)
        assertNull(wrongMapped.requiresCheckpoint)
        assertNull(ProjectionTruthMapper.pendingCount(wrongTypes))
    }

    @Test
    fun `home projection summaries preserve child statuses`() {
        assertEquals(
            "DEGRADED",
            ProjectionTruthMapper.projectionStatus(JSONObject().put("status", "DEGRADED")),
        )
        assertEquals("UNKNOWN", ProjectionTruthMapper.projectionStatus(JSONObject()))
        assertNull(ProjectionTruthMapper.projectionStatus(null))
        assertNull(ProjectionTruthMapper.aiConfigured(JSONObject()))
        assertNull(ProjectionTruthMapper.aiConfigured(null))
        assertTrue(ProjectionTruthMapper.aiConfigured(JSONObject().put("status", "AVAILABLE")) == true)
        assertTrue(ProjectionTruthMapper.aiConfigured(JSONObject().put("status", "UNAVAILABLE")) == false)
    }

    @Test
    fun `agent projections retain authoritative identity workspace activity protection and evidence`() {
        val agents = JSONObject(
            """
            {
              "items":[{
                "detected_identity":"PI d86173",
                "role":"EXECUTION_AGENT",
                "lifecycle":"RUNNING",
                "execution_domain_id":"windows-native",
                "workspace":{"status":"BOUND","workspace_id":"workspace-pi","reason_code":"WORKSPACE_BOUND"},
                "activity_observability":"OBSERVABLE",
                "latest_activity":{"event_id":"event-activity","type":"PROCESS_STARTED","timestamp":"2026-08-31T00:00:00Z"},
                "recent_activity_count":3,
                "activity_reason_code":"ACTIVITY_OBSERVED",
                "reason_code":"AGENT_DETECTED",
                "evidence_refs":["event-agent","event-activity"]
              }]
            }
            """.trimIndent(),
        )
        val supervision = JSONObject(
            """
            {
              "observed_agents":[{
                "detected_identity":"KIMI_CODE a1b2c3",
                "agent_ref":"external-agent-a1b2c3",
                "role":"EXECUTION_AGENT",
                "lifecycle":"RUNNING",
                "execution_domain_id":"docker-container-1",
                "workspace":{"status":"BOUND","workspace_id":"workspace-kimi","reason_code":"WORKSPACE_BOUND"},
                "supervision_status":"SUPERVISED",
                "supervision_session_id":"session-1",
                "policy_decision":"REVIEW",
                "pending_approval":true,
                "activity_observability":"OBSERVABLE",
                "latest_activity":{"event_id":"event-change","type":"OBSERVED_CHANGE","timestamp":"2026-08-31T00:01:00Z"},
                "recent_activity_count":5,
                "activity_reason_code":"ACTIVITY_OBSERVED",
                "protection_state":"RECOVERABLE",
                "verification_state":"VERIFIED",
                "latest_verified_change":{"event_id":"event-change","type":"OBSERVED_CHANGE","workspace_id":"workspace-kimi"},
                "latest_checkpoint":{"checkpoint_id":"checkpoint-9"},
                "evidence_refs":["event-agent","event-change"]
              }]
            }
            """.trimIndent(),
        )

        val environmentAgent = ProjectionTruthMapper.agents(agents).single()
        assertEquals("PI d86173", environmentAgent.identity)
        assertEquals("workspace-pi", environmentAgent.workspaceId)
        assertEquals("OBSERVABLE", environmentAgent.activityObservability)
        assertEquals("event-activity", environmentAgent.latestActivity?.eventId)
        assertEquals(listOf("event-agent", "event-activity"), environmentAgent.evidenceRefs)

        val supervised = ProjectionTruthMapper.supervisionAgents(supervision).single()
        assertEquals("external-agent-a1b2c3", supervised.agentRef)
        assertEquals("workspace-kimi", supervised.workspaceId)
        assertEquals("SUPERVISED", supervised.supervisionStatus)
        assertEquals("session-1", supervised.supervisionSessionId)
        assertEquals("REVIEW", supervised.policyDecision)
        assertTrue(supervised.pendingApproval == true)
        assertEquals("RECOVERABLE", supervised.protectionState)
        assertEquals("event-change", supervised.latestVerifiedChange?.eventId)
        assertEquals("checkpoint-9", supervised.latestCheckpointId)
        assertEquals(listOf("event-agent", "event-change"), supervised.evidenceRefs)
    }

    @Test
    fun `home and recovery facts come from authoritative projections`() {
        val changes = JSONObject(
            """{"items":[{"event_id":"event-latest","type":"OBSERVED_CHANGE","timestamp":"2026-08-31T00:02:00Z"}]}""",
        )
        val supervision = JSONObject(
            """{"items":[{"blocked_or_failed_reason":"POLICY_BLOCKED"},{"blocked_or_failed_reason":null}]}""",
        )
        val recovery = JSONObject(
            """
            {
              "workspace_id":"workspace-kimi",
              "protection_state":"RECOVERY_VERIFIED",
              "verification_state":"VERIFIED",
              "actual_restore_status":"VERIFIED",
              "action_eligible":true,
              "eligibility_reason_code":"RECOVERY_ACTION_ELIGIBLE",
              "coverage":{"counts":{"restorable":4,"audit_only":1,"excluded":0,"unreachable":0}},
              "latest_checkpoint":{"checkpoint_id":"checkpoint-9","evidence_refs":["event-cp"]},
              "evidence_refs":["event-cp","event-restore"]
            }
            """.trimIndent(),
        )

        assertEquals("event-latest", ProjectionTruthMapper.latestChange(changes)?.eventId)
        assertEquals(1, ProjectionTruthMapper.blockedOrFailedCount(supervision))
        val facts = ProjectionTruthMapper.recoveryFacts(recovery)
        assertEquals("workspace-kimi", facts.workspaceId)
        assertEquals("checkpoint-9", facts.latestCheckpointId)
        assertEquals("RECOVERY_VERIFIED", facts.protectionState)
        assertEquals("restorable=4 · audit_only=1 · excluded=0 · unreachable=0", facts.coverageSummary)
        assertEquals(listOf("event-cp", "event-restore"), facts.evidenceRefs)
    }

    @Test
    fun `authoritative evidence 404 remains not found`() {
        val mapped = ProjectionTruthMapper.evidenceHttpFailure(
            status = 404,
            reasonCode = "EVIDENCE_EVENT_NOT_FOUND",
            requestedEventId = "event-missing",
        )

        assertEquals(DataPhase.EMPTY, mapped.phase)
        assertEquals("NOT_FOUND", mapped.status)
        assertEquals("EVIDENCE_EVENT_NOT_FOUND", mapped.reasonCode)
        assertEquals("event-missing", mapped.eventId)
        val ambiguous = ProjectionTruthMapper.evidenceHttpFailure(
            status = 404,
            reasonCode = "DEVICE_HTTP_404",
            requestedEventId = "event-missing",
        )
        assertEquals(DataPhase.ERROR, ambiguous.phase)
        assertEquals("UNAVAILABLE", ambiguous.status)
        assertEquals("DEVICE_HTTP_404", ambiguous.reasonCode)
    }

    @Test
    fun `current supervision facts never come from latest verified history`() {
        val item = JSONObject(
            """
            {
              "supervision_session_id":"s-1",
              "status":"ACTIVE",
              "latest_verified_activity":{"event_id":"e-history","type":"OBSERVED_CHANGE"},
              "current_task":null,
              "current_phase":null,
              "current_action":null
            }
            """.trimIndent(),
        )

        val mapped = ProjectionTruthMapper.supervisionSession(item)

        assertEquals("e-history", mapped.latestVerifiedActivity?.eventId)
        assertNull(mapped.currentTask)
        assertNull(mapped.currentPhase)
        assertNull(mapped.currentAction)
    }

    @Test
    fun `changes map only the existing bounded workspace facts`() {
        val item = JSONObject(
            """
            {
              "event_id":"event-1",
              "timestamp":"2026-08-17T12:00:00Z",
              "actor":"workspace-observer",
              "subject":"target:opaque",
              "type":"OBSERVED_CHANGE",
              "result":"CHANGED",
              "checkpoint_id":"cp-1",
              "affected_objects":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
              "verification_summary":"PASS",
              "execution_domain_id":"windows-current",
              "attribution":"UNATTRIBUTED",
              "change_kind":"MODIFIED",
              "coverage_before":"RESTORABLE",
              "coverage_after":"RESTORABLE",
              "recovery_disposition":"RECOVERABLE",
              "workspace_id":"workspace-opaque",
              "evidence_refs":["event-1"]
            }
            """.trimIndent(),
        )

        val mapped = ProjectionTruthMapper.change(item)

        assertEquals("workspace-observer", mapped.actor)
        assertEquals("target:opaque", mapped.subject)
        assertEquals("cp-1", mapped.checkpointId)
        assertEquals("PASS", mapped.verificationSummary)
        assertEquals("windows-current", mapped.executionDomainId)
        assertEquals("UNATTRIBUTED", mapped.attribution)
        assertEquals("MODIFIED", mapped.changeKind)
        assertEquals("RESTORABLE", mapped.coverageBefore)
        assertEquals("RESTORABLE", mapped.coverageAfter)
        assertEquals("RECOVERABLE", mapped.recoveryDisposition)
        assertEquals("workspace-opaque", mapped.workspaceId)
        assertEquals(listOf("event-1"), mapped.evidenceRefs)
        assertEquals(1, mapped.affectedObjects?.size)
    }

    @Test
    fun `malformed authoritative string list remains unknown instead of partial`() {
        val item = JSONObject(
            """
            {
              "event_id":"event-malformed",
              "type":"OBSERVED_CHANGE",
              "affected_objects":["opaque-valid",7]
            }
            """.trimIndent(),
        )

        val mapped = ProjectionTruthMapper.change(item)

        assertNull(mapped.affectedObjects)
        assertEquals("event-malformed", mapped.file)
    }

    @Test
    fun `evidence maps sanitized workspace facts without raw payload`() {
        val dto = JSONObject(
            """
            {
              "status":"AVAILABLE",
              "event_id":"event-1",
              "event_type":"OBSERVED_CHANGE",
              "execution_domain_id":"windows-current",
              "sanitized_detail":{
                "affected_objects":["opaque-object"],
                "verification":"PASS",
                "attribution":"UNATTRIBUTED",
                "change_kind":"MODIFIED",
                "coverage_before":"RESTORABLE",
                "coverage_after":"AUDIT_ONLY",
                "recovery_disposition":"PARTIAL",
                "workspace_id":"workspace-opaque",
                "debug_detail":"must-not-map"
              },
              "related_evidence_refs":["related-1"]
            }
            """.trimIndent(),
        )

        val mapped = ProjectionTruthMapper.evidence(dto, "event-1")

        assertEquals(DataPhase.CONNECTED, mapped.phase)
        assertEquals("windows-current", mapped.executionDomainId)
        assertEquals("UNATTRIBUTED", mapped.attribution)
        assertEquals("MODIFIED", mapped.changeKind)
        assertEquals("RESTORABLE", mapped.coverageBefore)
        assertEquals("AUDIT_ONLY", mapped.coverageAfter)
        assertEquals("PARTIAL", mapped.recoveryDisposition)
        assertEquals("workspace-opaque", mapped.workspaceId)
        assertEquals(listOf("opaque-object"), mapped.affectedObjects)
        assertEquals(listOf("related-1"), mapped.relatedEvidenceRefs)
        assertFalse(mapped.toString().contains("must-not-map"))
    }

    @Test
    fun `malformed supervision action response cannot become success`() {
        val malformed = ProjectionTruthMapper.supervisionAction(
            JSONObject().put("status", "APPROVED"),
            expectedAction = "APPROVE_ONCE",
            expectedSessionId = "s-1",
        )

        assertFalse(malformed.succeeded)
        assertEquals("SUPERVISION_ACTION_RESPONSE_INVALID", malformed.reasonCode)
        assertFalse(
            ProjectionTruthMapper.supervisionAction(
                JSONObject().put("status", "APPROVED").put("consumed", true),
                expectedAction = "APPROVE_ONCE",
                expectedSessionId = "s-1",
            ).succeeded,
        )
        val canonical = JSONObject()
            .put("schema_version", "r4-p8-action-1")
            .put("action", "APPROVE_ONCE")
            .put("supervision_session_id", "s-1")
            .put("status", "APPROVED")
            .put("reason_code", "SUPERVISION_APPROVED_ONCE")
            .put("consumed", true)
        assertTrue(
            ProjectionTruthMapper.supervisionAction(
                canonical,
                expectedAction = "APPROVE_ONCE",
                expectedSessionId = "s-1",
            ).succeeded,
        )
        assertFalse(
            ProjectionTruthMapper.supervisionAction(canonical, "REJECT", "s-1").succeeded,
        )
        assertFalse(
            ProjectionTruthMapper.supervisionAction(canonical, "APPROVE_ONCE", "s-2").succeeded,
        )
    }

    private fun snapshot(status: JSONObject? = null) = DeviceLinkSnapshot(
        online = false,
        status = status,
        environment = null,
        agents = null,
        supervision = null,
        changes = null,
        checkpoints = null,
        recovery = null,
        aiAdvisory = null,
        reasonCode = "DEVICE_LINK_OFFLINE",
    )
}
