export type Backend = "llama_cpp" | "mlx" | "vllm" | "sglang" | "sd_cpp";
export type Modality = "text" | "vision" | "embedding" | "image";
export type InstanceState = "ready" | "loading" | "paused" | "stopped" | "error";

export interface InstanceEntry {
  id: string;
  /** RM-51: the catalog (models) entry this instance belongs to. */
  model_id: string;
  /** RM-70: the catalog's public, immutable name — what clients send as
   * `model`, and what a `model:<slug>` grant should reference. */
  model_slug: string;
  /** RM-70: this instance's handle within its model ("#1", "#2"). Display
   * only — never a routing key. */
  label: string;
  context_length: number;
  port: number;
  path: string;
  family: string;
  quantization: string;
  backend: Backend;
  modality: Modality;
  mmproj_path: string;
  downloaded: boolean;
  discovery: boolean;
  rss_estimate_mb: number | null;
  /** On-disk size of the downloaded file(s), summed across shards — null if
   * not downloaded, or the file is missing. See routes.py's _merge(). */
  file_size_bytes: number | null;
  backend_url: string;
  hf_repo: string;
  hf_sha256: string;
  hf_filenames: string[];
  pid: number | null;
  state: InstanceState;
  /** Why the last start attempt failed — set only when state is "error". */
  error_message: string | null;
  cpu_percent: number;
  rss_mb: number;
  uptime_s: number;
  gpu_percent: number | null;
  gpu_vram_mb: number | null;
  node: string;
}

/** Response shape from the start/stop/restart action endpoints — an InstanceEntry minus `node`. */
export type InstanceActionResult = Omit<InstanceEntry, "node">;

export interface RegisterModelRequest {
  /** RM-70: optional when `model_id` is set — manager-api derives the next
   * free instance id, so adding a replica never asks for a unique name. */
  id?: string;
  port: number;
  path?: string;
  context_length?: number;
  family?: string;
  quantization?: string;
  backend?: Backend;
  modality?: Modality;
  mmproj_path?: string;
  discovery?: boolean;
  hf_repo?: string;
  hf_sha256?: string;
  /** RM-51: create a new instance of this already-catalogued model instead
   * of registering a brand-new one — manager-api pulls path/family/
   * quantization/etc. from the catalog entry server-side. */
  model_id?: string;
}

/** PATCH /admin/api/nodes/{node}/models/{id} — every field optional, `id` excluded (it's the registry key, not editable in place). */
export type UpdateModelRequest = Partial<Omit<RegisterModelRequest, "id">>;
