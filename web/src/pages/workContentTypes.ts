export type WorkContentEntry = { id: string; title: string };
export type WorkArtifactEntry = WorkContentEntry & { version: number; requirement_version: number; created_at?: string };
