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
