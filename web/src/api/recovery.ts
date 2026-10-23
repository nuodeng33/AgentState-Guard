import type { ApiClient } from './client';
import type { ProtectionScope } from './execution';

export type RecoveryFile = { file_id: string; relative_path: string; category?: string; reason_code?: string };
export type DirectoryVersion = { id: string; created_at: string; available: boolean | number;
  metadata_available: boolean; details_included: boolean; files: RecoveryFile[] };
export type RecoverySelection = RecoveryFile & { before: RecoveryFile | null; after: RecoveryFile | null };
export type RecoveryPreview = { status: string; preview_id?: string; digest?: string; expires_at?: string;
  scope_id?: string; selection?: RecoverySelection[]; affected_work_ids?: string[]; reason_code?: string };
export type RecoveryOperation = { id: string; kind: string; scope_id: string | null; state: string;
  request_id?: string; created_at: string; reason_code?: string; steps?: { relative_path: string; before: RecoveryFile | null; after: RecoveryFile | null }[] };
export type RecoveryDiff = { text_diff: string | null; binary: boolean; truncated?: boolean };
export type RecoveryCommand = { request_id: string; preview_id: string; digest: string };
export const RECOVERY_PAGE_SIZE = 50;
export const recoveryActive = (state: string) => ['QUEUED', 'RUNNING', 'COMPENSATING'].includes(state);
export function recoveryApi(client: ApiClient) {
  return {
    scopes: () => client.get<{ items: ProtectionScope[] }>('/api/v1/protection/scopes'),
    versions: (scope: string, offset: number) => client.get<{ items: DirectoryVersion[] }>(
      `/api/v1/protection/scopes/${encodeURIComponent(scope)}/versions?limit=50&offset=${offset}&view=full`),
    operations: (offset: number) => client.get<{ items: RecoveryOperation[] }>(
      `/api/v1/recovery/operations?limit=50&offset=${offset}&view=summary`),
    operation: (id: string) => client.get<RecoveryOperation>(`/api/v1/recovery/operations/${encodeURIComponent(id)}`),
    preview: (body: { scope_id: string; mode: 'restore_version' | 'undo_operation'; file_ids: string[];
      version_id?: string; operation_id?: string }) => client.post<RecoveryPreview>('/api/v1/recovery/previews', body),
    diff: (preview: string, file: string) => client.get<RecoveryDiff>(
      `/api/v1/recovery/previews/${encodeURIComponent(preview)}/files/${encodeURIComponent(file)}/diff`),
    apply: (body: RecoveryCommand) => client.post<RecoveryOperation>('/api/v1/recovery/operations', body),
  };
}
