import type { Modality } from "./instance";

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
  family?: string;
  quantization?: string;
}

export interface StartDownloadResult {
  model_id: string;
  hf_repo: string;
  shard_count: number;
}

/** RM-51: a downloaded/known model — the catalog entry. Downloading no
 * longer auto-creates an instance; `instance_ids` lists whichever instances
 * (if any) the operator has since created from it. */
export interface ModelCatalogEntry {
  id: string;
  /** PRM-108: derived from the weights file when the file declares it
   * (a classifier head or a pooling type), so registering a downloaded model
   * starts from what it actually is rather than from the "text" default. */
  modality: Modality;
  path: string;
  family: string;
  quantization: string;
  downloaded: boolean;
  hf_repo: string;
  hf_sha256: string;
  hf_filenames: string[];
  mmproj_path: string;
  vae_path: string;
  clip_l_path: string;
  t5xxl_path: string;
  instance_ids: string[];
  node: string;
  file_size_bytes: number | null;
}

export interface ModelsConfig {
  downloads_dir: string;
  hf_token_env: string;
  ca_bundle: string;
}

export type UpdateModelsConfigRequest = Partial<ModelsConfig>;
