/**
 * Product API surface （冻结 V1 contract, docs/FRONTEND_BACKEND_CONTRACT.md).
 *
 * 所有函数仅作为 apiClient 的薄封装 + DTO 类型绑定。body 严格按 contract，
 * 不额外加字段；reason_code 一律保留 machine token 原值。
 */

import { apiClient, type ApiClient } from './client';
import type {
  AiAdvisory,
  AiModelsResult,
  AiTestResult,
  ChangesView,
  ControlledChangeApplyResult,
  ControlledChangePrep,
  DeviceLinkActionResult,
  DeviceLinkStatus,
  DiscoveryRefreshResult,
  DoctorView,
  EvidenceDetail,
  PairingInvitation,
  PairingStateResult,
  RecoveryActionResult,
  StatusView,
  SupervisionActionResult,
} from './types';

export function getStatus(client: ApiClient = apiClient) {
  return client.get<StatusView>('/api/status');
}
export function getDoctor(client: ApiClient = apiClient) {
  return client.get<DoctorView>('/api/doctor');
}
export function getChanges(client: ApiClient = apiClient) {
  return client.get<ChangesView>('/api/v1/changes');
}
export function getChangesPage(
  beforeSequence: number,
  client: ApiClient = apiClient,
  filters: {
    includeProcessActivity?: boolean;
    workspaceId?: string | null;
    checkpointId?: string | null;
  } = {},
) {
  const params = new URLSearchParams({ limit: '100', before_sequence: String(beforeSequence) });
  if (filters.includeProcessActivity) params.set('include_process_activity', 'true');
  if (filters.workspaceId) params.set('workspace_id', filters.workspaceId);
  if (filters.checkpointId) params.set('checkpoint_id', filters.checkpointId);
  return client.get<ChangesView>(`/api/v1/changes?${params.toString()}`);
}
export function getEvidence(eventId: string, client: ApiClient = apiClient) {
  return client.get<EvidenceDetail>(`/api/v1/evidence/${encodeURIComponent(eventId)}`);
}
export function refreshDiscovery(client: ApiClient = apiClient) {
  return client.post<DiscoveryRefreshResult>('/api/v1/discovery/refresh', {});
}

export function createRecoveryCheckpoint(client: ApiClient = apiClient) {
  return client.post<RecoveryActionResult>('/api/v1/recovery/checkpoints', {});
}
export function testRecovery(checkpointId: string, client: ApiClient = apiClient) {
  return client.post<RecoveryActionResult>(`/api/v1/recovery/${encodeURIComponent(checkpointId)}/test`, {});
}
export function restoreRecovery(checkpointId: string, client: ApiClient = apiClient) {
  return client.post<RecoveryActionResult>(`/api/v1/recovery/${encodeURIComponent(checkpointId)}/restore`, {
    confirm: true,
  });
}

export function prepareControlledChange(content: string, client: ApiClient = apiClient) {
  return client.post<ControlledChangePrep>('/api/v1/supervision/changes', { content });
}
export function applyControlledChange(sessionId: string, content: string, client: ApiClient = apiClient) {
  return client.post<ControlledChangeApplyResult>(
    `/api/v1/supervision/${encodeURIComponent(sessionId)}/apply`,
    { content },
  );
}
export function approveSupervisionOnce(sessionId: string, actionRef: string, client: ApiClient = apiClient) {
  return client.post<SupervisionActionResult>(
    `/api/v1/supervision/${encodeURIComponent(sessionId)}/approve-once`,
    { action_ref: actionRef },
  );
}
export function rejectSupervision(sessionId: string, actionRef: string, client: ApiClient = apiClient) {
  return client.post<SupervisionActionResult>(
    `/api/v1/supervision/${encodeURIComponent(sessionId)}/reject`,
    { action_ref: actionRef },
  );
}

export function getDevices(client: ApiClient = apiClient) {
  return client.get<DeviceLinkStatus>('/api/v1/devices');
}
export function enableDeviceLink(client: ApiClient = apiClient) {
  return client.post<DeviceLinkStatus>('/api/v1/device-link/enable', {});
}
export function disableDeviceLink(client: ApiClient = apiClient) {
  return client.post<DeviceLinkStatus>('/api/v1/device-link/disable', {});
}
export function refreshDeviceNetwork(client: ApiClient = apiClient) {
  return client.post<DeviceLinkStatus>('/api/v1/device-link/network/refresh', {});
}
export function createPairing(client: ApiClient = apiClient) {
  return client.post<PairingInvitation>('/api/v1/device-link/pairings', {});
}
export function getPairing(sessionId: string, client: ApiClient = apiClient) {
  return client.get<PairingStateResult>(`/api/v1/device-link/pairings/${encodeURIComponent(sessionId)}`);
}
export function confirmPairing(sessionId: string, confirm: boolean, client: ApiClient = apiClient) {
  return client.post<PairingStateResult>(`/api/v1/device-link/pairings/${encodeURIComponent(sessionId)}/confirm`, {
    confirm,
  });
}
export function cancelPairing(sessionId: string, client: ApiClient = apiClient) {
  return client.post<PairingStateResult>(`/api/v1/device-link/pairings/${encodeURIComponent(sessionId)}/cancel`, {});
}
export function revokeDevice(deviceUuid: string, client: ApiClient = apiClient) {
  return client.post<DeviceLinkActionResult>(
    `/api/v1/device-link/devices/${encodeURIComponent(deviceUuid)}/revoke`,
    {},
  );
}

export function aiTest(baseUrl: string, apiKey: string, model: string, client: ApiClient = apiClient) {
  return client.post<AiTestResult>('/api/ai/test', { base_url: baseUrl, api_key: apiKey, model });
}
export function aiModels(baseUrl: string, apiKey: string, client: ApiClient = apiClient) {
  return client.post<AiModelsResult>('/api/ai/models', { base_url: baseUrl, api_key: apiKey });
}
export function aiAnalyze(client: ApiClient = apiClient) {
  return client.post<AiAdvisory>('/api/ai/analyze', {});
}

/** 组装 contract 规定的 canonical pairing URI（不含后端未发明的字段）。 */
export function buildPairUri(inv: PairingInvitation): string {
  const endpoint = new URL(inv.endpoint);
  const params = new URLSearchParams({
    v: String(inv.protocol_version),
    host: endpoint.hostname,
    port: endpoint.port || '8788',
    uuid: inv.desktop_uuid,
    pub: inv.desktop_public_key_der,
    sign_fp: inv.desktop_signing_fingerprint,
    tls_fp: inv.tls_spki_fingerprint,
    sid: inv.session_id,
    ticket: inv.ticket,
    exp: String(inv.expires_at_epoch),
  });
  return `agentstate://pair?${params.toString()}`;
}
