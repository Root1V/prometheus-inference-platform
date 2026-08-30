/** RM-48 — Models page: discover/download/manage models from Hugging Face. */

export interface HfSearchResult {
  id: string;
  downloads: number | null;
  likes: number | null;
  last_modified: string | null;
}

export interface HfFile {
  filename: string;
  quantization: string;
  size_bytes: number | null;
}

export type ModelSort = "downloads" | "likes" | "created_at" | "last_modified" | "trending_score";

export interface HfModelCard {
  repo_id: string;
  text: string;
  metadata: Record<string, unknown>;
}

export type DownloadStatus =
  | "queued"
  | "downloading"
  | "verifying"
  | "done"
  | "failed"
  | "cancelled"
  | "paused";

export interface DownloadEntry {
  model_id: string;
  hf_repo: string;
  hf_filename: string;
  total_bytes: number;
  downloaded_bytes: number;
  progress: number;
  status: DownloadStatus;
  error: string | null;
  speed_bps: number;
  eta_seconds: number | null;
}

export interface StartDownloadRequest {
  repo_id: string;
  filename: string;
  model_id?: string;
  context_length?: number;
  family?: string;
  quantization?: string;
  modality?: "text" | "vision" | "embedding";
}

export interface StartDownloadResult {
  model_id: string;
  port: number;
  hf_repo: string;
  shard_count: number;
}

export interface ModelsConfig {
  downloads_dir: string;
  hf_token_env: string;
  ca_bundle: string;
}

export type UpdateModelsConfigRequest = Partial<ModelsConfig>;
