package com.agentstate.guard.ui.link

import android.content.Context
import com.agentstate.guard.network.BindingStore
import com.agentstate.guard.network.BoundDeviceRediscovery
import com.agentstate.guard.network.DeviceClientFactory
import com.agentstate.guard.network.DeviceLinkClient
import com.agentstate.guard.network.DeviceLinkHttpException
import com.agentstate.guard.network.DeviceLinkRepository
import com.agentstate.guard.network.DeviceLinkSnapshot
import com.agentstate.guard.network.QrPayload
import com.agentstate.guard.network.AndroidKeyStoreSigner
import com.agentstate.guard.network.SharedPreferencesBindingStore
import com.agentstate.guard.ui.state.AiAdvisoryUiState
import com.agentstate.guard.ui.state.ChangesUiState
import com.agentstate.guard.ui.state.CheckpointUi
import com.agentstate.guard.ui.state.CheckpointsUiState
import com.agentstate.guard.ui.state.ConnectionUiState
import com.agentstate.guard.ui.state.DataPhase
import com.agentstate.guard.ui.state.EnvironmentItemUi
import com.agentstate.guard.ui.state.EnvironmentUiState
import com.agentstate.guard.ui.state.EvidenceUiState
import com.agentstate.guard.ui.state.HomeUiState
import com.agentstate.guard.ui.state.LinkedDesktop
import com.agentstate.guard.ui.state.RecoveryUiState
import com.agentstate.guard.ui.state.SupervisionActionUiResult
import com.agentstate.guard.ui.state.SupervisionAgentUi
import com.agentstate.guard.ui.state.SupervisionUiState
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Production adapter: the only bridge between the Compose shell and the
 * [DeviceLinkRepository]. It contains no HTTP/TLS/crypto — the network layer
 * owns all of that. Every value shown is mapped verbatim from a backend
 * projection; the last completed refresh feeds every surface, so all
 * projections derive from one consistent snapshot.
 *
 * Adapter methods are suspend shells around blocking repository calls,
 * confined to [Dispatchers.IO]; the Compose main thread never blocks.
 * Refresh is single-flight: concurrent callers share one repository round.
 */
class RepositoryDeviceLinkUiAdapter(
    private val repository: DeviceLinkRepository,
    private val bindings: BindingStore,
    private val offload: CoroutineDispatcher = Dispatchers.IO,
) : DeviceLinkUiAdapter {

    /** In-flight pairing: one at a time, dropped on finish/cancel. */
    private data class PairingInFlight(
        val payload: QrPayload,
        val androidUuid: String,
        val state: PairingUiState,
    )

    @Volatile private var snapshot: DeviceLinkSnapshot? = null
    @Volatile private var syncedAtEpochMs: Long? = null
    @Volatile private var pairing: PairingInFlight? = null

    private val refreshMutex = Mutex()

    private suspend fun <T> blocking(block: () -> T): T = withContext(offload) { block() }

    /** Latest snapshot; refreshes only when nothing has been fetched yet. */
    private suspend fun currentSnapshot(): DeviceLinkSnapshot =
        snapshot ?: performRefresh()

    /** Explicit refresh (app start, resume, pull); never silent background. */
    override suspend fun refresh() {
        performRefresh()
    }

    private suspend fun performRefresh(): DeviceLinkSnapshot = refreshMutex.withLock {
        val fresh = try {
            blocking { repository.refresh() }
        } catch (error: DeviceLinkHttpException) {
            offlineLastKnown(error.reasonCode)
        } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
            // Transport/mutual-auth failures surface as the repository's own
            // client-side offline token; raw exception text never reaches the UI.
            offlineLastKnown("DEVICE_LINK_OFFLINE")
        }
        snapshot = fresh
        if (fresh.online) syncedAtEpochMs = System.currentTimeMillis()
        fresh
    }

    private fun offlineLastKnown(reason: String): DeviceLinkSnapshot {
        val previous = snapshot
        return previous?.copy(online = false, reasonCode = reason) ?: DeviceLinkSnapshot(
            online = false,
            status = null,
            environment = null,
            agents = null,
            supervision = null,
            changes = null,
            checkpoints = null,
            recovery = null,
            aiAdvisory = null,
            reasonCode = reason,
        )
    }

    private fun phaseOf(snapshot: DeviceLinkSnapshot, projection: JSONObject?): DataPhase =
        ProjectionTruthMapper.phase(snapshot.online, projection)

    private fun lastKnownOf(snapshot: DeviceLinkSnapshot): Boolean =
        ProjectionTruthMapper.isLastKnown(snapshot)

    // ---- Per-surface projections ---------------------------------------------

    override suspend fun linkedDesktop(): LinkedDesktop? {
        val binding = blocking { bindings.load() } ?: return null
        return LinkedDesktop(
            id = binding.desktopUuid,
            displayName = binding.desktopUuid,
            desktopUuid = binding.desktopUuid,
            signingFingerprint = binding.desktopSigningFingerprint,
            endpoint = "${binding.endpoint.host}:${binding.endpoint.port}",
        )
    }

    override suspend fun connectionState(): ConnectionUiState {
        val binding = blocking { bindings.load() }
        val current = currentSnapshot()
        return ConnectionUiState(
            phase = if (binding == null) DataPhase.EMPTY else phaseOf(current, current.status),
            paired = binding != null,
            online = current.online,
            lastKnown = lastKnownOf(current),
            authRequired = current.authRequired,
            reasonCode = current.reasonCode,
            desktopUuid = binding?.desktopUuid,
            signingFingerprint = binding?.desktopSigningFingerprint,
            endpoint = binding?.let { "${it.endpoint.host}:${it.endpoint.port}" },
            observedAt = observedOf(current),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    override suspend fun homeState(): HomeUiState {
        val current = currentSnapshot()
        return HomeUiState(
            phase = phaseOf(current, current.status),
            desktopName = blocking { bindings.load() }?.desktopUuid,
            overallStatus = current.status?.optStringOpt("status"),
            runtimeSummary = ProjectionTruthMapper.projectionStatus(current.environment),
            agentsSummary = ProjectionTruthMapper.projectionStatus(current.agents),
            supervisionStatus = ProjectionTruthMapper.projectionStatus(current.supervision),
            pendingSupervision = current.supervision?.let { ProjectionTruthMapper.pendingCount(it) },
            changesStatus = ProjectionTruthMapper.projectionStatus(current.changes),
            changesCount = current.changes?.optItemsCount(),
            lastCheckpoint = current.checkpoints?.optFirstCheckpointId()
                ?: current.recovery?.optJSONObjectOpt("latest_checkpoint")
                    ?.optStringOpt("checkpoint_id"),
            aiStatus = current.aiAdvisory?.optStringOpt("status"),
            recoveryProjectionStatus = ProjectionTruthMapper.projectionStatus(current.recovery),
            recoveryStatus = current.recovery?.optStringOpt("recovery_level"),
            reasonCode = current.reasonCode,
            lastKnown = lastKnownOf(current),
            observedAt = observedOf(current),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    override suspend fun environmentState(): EnvironmentUiState {
        val current = currentSnapshot()
        val environment = current.environment ?: return unavailableEnvironment(current)
        val items = environment.optJSONArraySafe("items")?.mapObjects { item ->
            EnvironmentItemUi(
                name = item.optStringOpt("runtime_type")
                    ?: item.optStringOpt("execution_domain_id")
                    ?: "UNKNOWN",
                version = null,
                status = item.optStringOpt("availability") ?: "UNKNOWN",
                reasonCode = item.optStringOpt("reason_code"),
                observedAt = item.optStringOpt("observed_at"),
            )
        }.orEmpty()
        return EnvironmentUiState(
            phase = phaseOf(current, environment),
            items = items,
            reasonCode = environment.optStringOpt("reason_code") ?: current.reasonCode,
            lastKnown = lastKnownOf(current),
            observedAt = environment.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    private fun unavailableEnvironment(current: DeviceLinkSnapshot) = EnvironmentUiState(
        phase = phaseOf(current, current.environment),
        reasonCode = current.reasonCode,
        lastKnown = lastKnownOf(current),
        syncedAtEpochMs = syncedAtEpochMs,
    )

    override suspend fun changesState(): ChangesUiState {
        val current = currentSnapshot()
        val changes = current.changes ?: return unavailableChanges(current)
        val items = changes.optJSONArraySafe("items")?.mapObjects { item ->
            ProjectionTruthMapper.change(item)
        }.orEmpty()
        return ChangesUiState(
            phase = phaseOf(current, changes),
            items = items,
            reasonCode = changes.optStringOpt("reason_code") ?: current.reasonCode,
            lastKnown = lastKnownOf(current),
            observedAt = changes.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    private fun unavailableChanges(current: DeviceLinkSnapshot) = ChangesUiState(
        phase = phaseOf(current, current.changes),
        reasonCode = current.reasonCode,
        lastKnown = lastKnownOf(current),
        syncedAtEpochMs = syncedAtEpochMs,
    )

    override suspend fun supervisionState(): SupervisionUiState {
        val current = currentSnapshot()
        val supervision = current.supervision ?: return unavailableSupervision(current)
        val sessions = supervision.optJSONArraySafe("items")?.mapObjects { item ->
            ProjectionTruthMapper.supervisionSession(item)
        }.orEmpty()
        val agents = supervision.optJSONArraySafe("observed_agents")?.mapObjects { item ->
            SupervisionAgentUi(
                identity = item.optStringOpt("detected_identity")
                    ?: item.optStringOpt("identity")
                    ?: "UNKNOWN",
                role = item.optStringOpt("role"),
                lifecycle = item.optStringOpt("lifecycle"),
                observedAt = item.optStringOpt("observed_at"),
            )
        }.orEmpty()
        val recent = supervision.optJSONArraySafe("recent_verified_activities")
            ?.mapObjects { item -> ProjectionTruthMapper.verifiedActivity(item) }
            .orEmpty()
        return SupervisionUiState(
            phase = phaseOf(current, supervision),
            pendingCount = ProjectionTruthMapper.pendingCount(supervision),
            reasonCode = supervision.optStringOpt("reason_code") ?: current.reasonCode,
            sessions = sessions,
            observedAgents = agents,
            recentActivities = recent,
            lastKnown = lastKnownOf(current),
            observedAt = supervision.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    private fun unavailableSupervision(current: DeviceLinkSnapshot) = SupervisionUiState(
        phase = phaseOf(current, current.supervision),
        reasonCode = current.reasonCode,
        lastKnown = lastKnownOf(current),
        syncedAtEpochMs = syncedAtEpochMs,
    )

    override suspend fun checkpointsState(): CheckpointsUiState {
        val current = currentSnapshot()
        val checkpoints = current.checkpoints ?: return unavailableCheckpoints(current)
        val items = checkpoints.optJSONArraySafe("items")?.mapObjects { item ->
            CheckpointUi(
                id = item.optStringOpt("checkpoint_id") ?: "UNKNOWN",
                label = item.optStringOpt("execution_domain_id")
                    ?: item.optStringOpt("checkpoint_id")
                    ?: "UNKNOWN",
                createdAt = item.optStringOpt("created_at") ?: "",
                status = item.optStringOpt("status"),
                manifestIntegrity = item.optStringOpt("manifest_integrity"),
            )
        }.orEmpty()
        return CheckpointsUiState(
            phase = phaseOf(current, checkpoints),
            items = items,
            reasonCode = checkpoints.optStringOpt("reason_code") ?: current.reasonCode,
            lastKnown = lastKnownOf(current),
            observedAt = checkpoints.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    private fun unavailableCheckpoints(current: DeviceLinkSnapshot) = CheckpointsUiState(
        phase = phaseOf(current, current.checkpoints),
        reasonCode = current.reasonCode,
        lastKnown = lastKnownOf(current),
        syncedAtEpochMs = syncedAtEpochMs,
    )

    override suspend fun recoveryState(): RecoveryUiState {
        val current = currentSnapshot()
        val recovery = current.recovery ?: return RecoveryUiState(
            phase = phaseOf(current, current.recovery),
            reasonCode = current.reasonCode,
            lastKnown = lastKnownOf(current),
            syncedAtEpochMs = syncedAtEpochMs,
        )
        return RecoveryUiState(
            phase = phaseOf(current, recovery),
            recoveryLevel = recovery.optStringOpt("recovery_level"),
            reasonCode = recovery.optStringOpt("reason_code") ?: current.reasonCode,
            actualRestoreStatus = recovery.optStringOpt("actual_restore_status"),
            testRestoreStatus = recovery.optStringOpt("test_restore_status"),
            trustedBaselineStatus = recovery.optStringOpt("trusted_baseline_status"),
            checkpointCount = recovery.optIntSafe("checkpoint_count"),
            verifiedAt = recovery.optStringOpt("verified_at"),
            lastKnown = lastKnownOf(current),
            observedAt = recovery.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    override suspend fun aiAdvisoryState(): AiAdvisoryUiState {
        val current = currentSnapshot()
        val advisory = current.aiAdvisory ?: return AiAdvisoryUiState(
            phase = phaseOf(current, current.aiAdvisory),
            configured = null,
            reasonCode = current.reasonCode,
            lastKnown = lastKnownOf(current),
            syncedAtEpochMs = syncedAtEpochMs,
        )
        return AiAdvisoryUiState(
            phase = phaseOf(current, advisory),
            configured = ProjectionTruthMapper.aiConfigured(advisory),
            summary = advisory.optStringOpt("summary"),
            severity = advisory.optStringOpt("severity"),
            reasonCode = advisory.optStringOpt("reason_code") ?: current.reasonCode,
            analyzedAt = advisory.optStringOpt("analyzed_at"),
            lastKnown = lastKnownOf(current),
            observedAt = advisory.optStringOpt("observed_at"),
            syncedAtEpochMs = syncedAtEpochMs,
        )
    }

    override suspend fun evidenceState(eventId: String): EvidenceUiState {
        // Canonical lookup key is exactly the Changes item's event_id; the UI
        // surface never builds or guesses one. A failed detail read never
        // mutates the parent projection and never falls back to raw data.
        val dto = try {
            blocking { repository.evidence(eventId) }
        } catch (error: DeviceLinkHttpException) {
            return ProjectionTruthMapper.evidenceHttpFailure(
                status = error.status,
                reasonCode = error.reasonCode,
                requestedEventId = eventId,
            )
        } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
            return EvidenceUiState(
                phase = DataPhase.ERROR,
                status = "UNAVAILABLE",
                reasonCode = evidenceFailureReasonCode(error),
                eventId = eventId,
            )
        }
        return ProjectionTruthMapper.evidence(dto, eventId)
    }

    // ---- The only two Android mutations ---------------------------------------

    override suspend fun approveOnce(
        sessionId: String,
        actionRef: String,
    ): SupervisionActionUiResult = supervisionAction(
        expectedAction = "APPROVE_ONCE",
        expectedSessionId = sessionId,
    ) {
        repository.approveOnce(sessionId, actionRef)
    }

    override suspend fun rejectSupervision(
        sessionId: String,
        actionRef: String,
    ): SupervisionActionUiResult = supervisionAction(
        expectedAction = "REJECT",
        expectedSessionId = sessionId,
    ) {
        repository.reject(sessionId, actionRef)
    }

    private suspend fun supervisionAction(
        expectedAction: String,
        expectedSessionId: String,
        block: () -> JSONObject,
    ): SupervisionActionUiResult = try {
        val result = blocking(block)
        // Never serve stale supervision data after a mutation.
        snapshot = null
        performRefresh()
        ProjectionTruthMapper.supervisionAction(result, expectedAction, expectedSessionId)
    } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
        SupervisionActionUiResult(
            succeeded = false,
            status = null,
            reasonCode = supervisionFailureReasonCode(error),
        )
    }

    // ---- Unpair -----------------------------------------------------------------

    override suspend fun unpair() {
        blocking { repository.unpair() }
        snapshot = null
        syncedAtEpochMs = null
        pairing = null
    }

    // ---- Pairing state machine --------------------------------------------------

    override suspend fun beginScanPairing(payloadText: String): PairingUiState =
        try {
            val payload = QrPayload.parse(payloadText)
                ?: return PairingUiState(PairingPhase.ERROR, reasonCode = "PAIR_QR_INVALID")
            val androidUuid = blocking { bindings.load()?.androidUuid }
                ?: SharedPreferencesBindingStore.newAndroidUuid()
            val state = blocking {
                repository.beginPairing(payload, androidUuid)
                val sasCode = repository.startSas(payload)
                PairingUiState(
                    phase = PairingPhase.SAS_PENDING,
                    pairingId = payload.sessionId,
                    desktopName = payload.desktopUuid,
                    sasCode = sasCode,
                    expiresAtEpochMs = payload.expiryEpochSeconds * 1000L,
                )
            }
            pairing = PairingInFlight(payload, androidUuid, state)
            state
        } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
            pairing = null
            PairingUiState(
                phase = PairingPhase.ERROR,
                reasonCode = pairingFailureReasonCode(error),
            )
        }

    override suspend fun pollPairing(pairingId: String): PairingUiState {
        val session = pairing?.takeIf { it.payload.sessionId == pairingId }
            ?: return PairingUiState(PairingPhase.ERROR, reasonCode = "PAIRING_NOT_STARTED")
        if (isExpired(session)) {
            pairing = null
            return PairingUiState(PairingPhase.EXPIRED, reasonCode = "PAIRING_EXPIRED")
        }
        return when (session.state.phase) {
            // The Android side already confirmed; poll re-drives the idempotent
            // confirm so a desktop confirmation can complete the pairing.
            PairingPhase.CONFIRMING -> drivePairing(session)
            else -> session.state
        }
    }

    override suspend fun confirmSas(pairingId: String): PairingUiState {
        val session = pairing?.takeIf { it.payload.sessionId == pairingId }
            ?: return PairingUiState(PairingPhase.ERROR, reasonCode = "PAIRING_NOT_STARTED")
        return drivePairing(session)
    }

    private suspend fun drivePairing(session: PairingInFlight): PairingUiState =
        try {
            when (blocking { repository.confirmSas(session.payload, true) }) {
                "confirmed_both", "consumed" -> completePairing(session)
                "rejected" -> finishPairing(session, PairingPhase.REJECTED, "REJECTED")
                "expired", "cancelled" -> finishPairing(session, PairingPhase.EXPIRED, "PAIRING_EXPIRED")
                // Still waiting for the desktop side to confirm the same SAS.
                else -> stayConfirming(session)
            }
        } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
            pairing = null
            PairingUiState(
                phase = PairingPhase.ERROR,
                reasonCode = pairingFailureReasonCode(error),
            )
        }

    private fun stayConfirming(session: PairingInFlight): PairingUiState {
        val state = session.state.copy(phase = PairingPhase.CONFIRMING)
        pairing = session.copy(state = state)
        return state
    }

    private suspend fun completePairing(session: PairingInFlight): PairingUiState {
        blocking {
            repository.completePairing(session.payload, session.androidUuid, DEVICE_DISPLAY_NAME)
        }
        pairing = null
        snapshot = null
        performRefresh()
        return PairingUiState(phase = PairingPhase.PAIRED, reasonCode = "PAIRED")
    }

    private suspend fun finishPairing(
        session: PairingInFlight,
        phase: PairingPhase,
        reasonCode: String,
    ): PairingUiState {
        pairing = null
        return PairingUiState(phase = phase, reasonCode = reasonCode)
    }

    private fun isExpired(session: PairingInFlight): Boolean =
        System.currentTimeMillis() >= session.payload.expiryEpochSeconds * 1000L

    override suspend fun rejectSas(pairingId: String): PairingUiState {
        val session = pairing?.takeIf { it.payload.sessionId == pairingId }
            ?: return PairingUiState(PairingPhase.ERROR, reasonCode = "PAIRING_NOT_STARTED")
        return try {
            blocking { repository.confirmSas(session.payload, false) }
            pairing = null
            PairingUiState(phase = PairingPhase.REJECTED, reasonCode = "REJECTED")
        } catch (@Suppress("TooGenericExceptionCaught") error: Exception) {
            pairing = null
            PairingUiState(
                phase = PairingPhase.ERROR,
                reasonCode = pairingFailureReasonCode(error),
            )
        }
    }

    override suspend fun cancelPairing(pairingId: String) {
        if (pairing?.payload?.sessionId == pairingId) pairing = null
    }

    // ---- JSON helpers -------------------------------------------------------------

    private fun JSONObject.optStringOpt(key: String): String? =
        if (has(key) && !isNull(key)) optString(key) else null

    private fun JSONObject.optJSONObjectOpt(key: String): JSONObject? =
        if (has(key) && !isNull(key)) optJSONObject(key) else null

    private fun JSONObject.optJSONArraySafe(key: String): JSONArray? =
        if (has(key) && !isNull(key)) optJSONArray(key) else null

    private fun JSONObject.optIntSafe(key: String): Int? =
        if (has(key) && !isNull(key) && get(key) is Number) optInt(key) else null

    private fun JSONObject.optItemsCount(): Int? = optJSONArraySafe("items")?.length()


    private fun JSONObject.optFirstCheckpointId(): String? {
        val items = optJSONArraySafe("items") ?: return null
        if (items.length() == 0) return null
        return items.optJSONObject(0)?.optStringOpt("checkpoint_id")
    }


    private fun <T> JSONArray.mapObjects(map: (JSONObject) -> T): List<T> {
        val result = ArrayList<T>(length())
        for (index in 0 until length()) {
            val item = optJSONObject(index) ?: continue
            result += map(item)
        }
        return result
    }

    private fun observedOf(snapshot: DeviceLinkSnapshot): String? = listOf(
        snapshot.supervision,
        snapshot.environment,
        snapshot.changes,
        snapshot.checkpoints,
        snapshot.recovery,
        snapshot.aiAdvisory,
        snapshot.status,
    ).firstNotNullOfOrNull { it?.optStringOpt("observed_at") }

    companion object {
        /** Display name sent at pairing completion; server stores it verbatim. */
        private const val DEVICE_DISPLAY_NAME = "Android"

        /**
         * Build the production adapter from the Application context: durable
         * binding store, KeyStore identity, exact-UUID rediscovery, and the
         * pinned HTTPS client — created once and remembered by the shell.
         */
        fun provideAdapter(context: Context): RepositoryDeviceLinkUiAdapter {
            val preferences = context.getSharedPreferences(
                "device_link_binding", Context.MODE_PRIVATE
            )
            val bindings = SharedPreferencesBindingStore(preferences)
            val signer = AndroidKeyStoreSigner("device_link_identity")
            val repository = DeviceLinkRepository(
                bindings = bindings,
                signer = signer,
                rediscovery = BoundDeviceRediscovery(),
                clients = DeviceClientFactory { endpoint -> DeviceLinkClient(endpoint) },
            )
            return RepositoryDeviceLinkUiAdapter(repository, bindings)
        }
    }
}
