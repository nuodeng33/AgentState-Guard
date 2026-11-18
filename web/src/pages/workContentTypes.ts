export type WorkContentEntry = { id: string; title: string };
export type WorkArtifactEntry = WorkContentEntry & { version: number; requirement_version: number; created_at?: string };

export type WorkArtifactRef = { artifact_id: string; version: number };
export type WorkReview = {
  id: string; submission_id: string; requirement_version: number; reviewer: string;
  decision: 'APPROVED' | 'CHANGES_REQUESTED'; created_at: string; body: string;
  refs: { artifacts: WorkArtifactRef[]; files: unknown[] };
};
