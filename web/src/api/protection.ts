import type { ApiClient } from './client';
import type { ProtectionScope } from './execution';

export type ProtectionSource = {
  source_id: string; kind: string; label: string; running: boolean; connected: boolean;
  capabilities: { scan: boolean; apply: boolean };
};
export type ProtectionCandidate = {
  source_id: string; path: string; basis: string; session_id: string;
  observed_at: string; current: boolean; registration_required: boolean;
};
export type ProtectionCandidates = {
  sources: ProtectionSource[]; items: ProtectionCandidate[]; manual_registration: boolean;
  limit: number; offset: number; has_more: boolean;
};
export type ProtectionReceipt = { scope_id: string; revision: number; reused?: boolean };
type CommandContext = { sourceId: string; path: string; receipt?: ProtectionReceipt };
export type ProtectionScopeCommand = CommandContext & (
  { kind: 'register'; payload: { request_id: string; source_id: string; path: string } } |
  { kind: 'configure'; scopeId: string; payload: { request_id: string; expected_revision: number;
    enabled: boolean; retention_days: number; quota_bytes: number } }
);
export const PROTECTION_MIB = 1048576;
export function protectionApi(client: ApiClient) {
  return {
    candidates: (offset = 0) => client.get<ProtectionCandidates>(
      `/api/v1/protection/scopes/candidates?limit=50&offset=${offset}`),
    scopes: () => client.get<{ items: ProtectionScope[] }>('/api/v1/protection/scopes'),
    submit: (command: ProtectionScopeCommand) => client.post<ProtectionReceipt>(
      command.kind === 'register' ? '/api/v1/protection/scopes' :
        `/api/v1/protection/scopes/${encodeURIComponent(command.scopeId)}/configure`,
      command.payload),
  };
}
