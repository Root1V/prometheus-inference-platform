export type Backend = "llama_cpp" | "mlx" | "vllm" | "sglang";
export type Modality = "text" | "vision" | "embedding";
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
  log_level: string;
  downloaded: boolean;
  discovery: boolean;
  rss_estimate_mb: number | null;
  backend_url: string;
  hf_repo: string;
  hf_filename: string;
  hf_sha256: string;
  hf_filenames: string[];
  pid: number | null;
  state: InstanceState;
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
  hf_filename?: string;
  hf_sha256?: string;
}
