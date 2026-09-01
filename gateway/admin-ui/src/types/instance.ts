export type Backend = "llama_cpp" | "mlx" | "vllm" | "sglang" | "sd_cpp";
export type Modality = "text" | "vision" | "embedding" | "image";
export type InstanceState = "ready" | "loading" | "paused" | "stopped" | "error";

export interface InstanceEntry {
  id: string;
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
  id: string;
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
}

/** PATCH /admin/api/nodes/{node}/models/{id} — every field optional, `id` excluded (it's the registry key, not editable in place). */
export type UpdateModelRequest = Partial<Omit<RegisterModelRequest, "id">>;
