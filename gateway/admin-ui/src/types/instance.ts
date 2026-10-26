// These three stay on one line each: gateway/tests/test_engine_list.py parses
// them with `export type X = ([^;]*);` to prove the UI offers exactly what the
// manager registry accepts. Wrapping them — or putting a `;` in a comment
// inside one — silently blinds that guard. PRM-219 did both and the guard
// caught it, which is the whole reason it exists.
export type Backend = "llama_cpp" | "mlx" | "vllm" | "sglang" | "sd_cpp" | "hf_serve" | "tei" | "laya";
/** PRM-219 adds `nli`: raw (premise, hypothesis) entailment. `zero_shot` is the
 *  pipeline built on a model like this — this is the model underneath. */
export type Modality = "text" | "vision" | "embedding" | "image" | "rerank" | "classification" | "zero_shot" | "nli" | "typed_decision";
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

/** PRM-281: GET /admin/api/nodes/{node}/models/{model_id}/instance-defaults —
 * what a new instance of a model gets when the operator only picks the model.
 * The node works these out (only it knows which ports are taken), and applies
 * the same ones to whatever a registration leaves out. */
export interface InstanceDefaults {
  id: string;
  port: number;
  context_length: number;
  /** False when the file declares no trained context and this is the 4096 fallback. */
  context_from_model: boolean;
  /** An existing instance's engine, or null when the node's own list decides. */
  backend: Backend | null;
}

export interface RegisterModelRequest {
  /** RM-70: optional when `model_id` is set — manager-api derives the next
   * free instance id, so adding a replica never asks for a unique name. */
  id?: string;
  /** PRM-281: optional with `model_id` — the node picks a free one. */
  port?: number;
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
  /** RM-70: the model's public routing name. Settable once, while it is still
   * the id the migration backfilled; frozen afterwards. */
  slug?: string;
}

/** PATCH /admin/api/nodes/{node}/models/{id} — every field optional, `id` excluded (it's the registry key, not editable in place). */
export type UpdateModelRequest = Partial<Omit<RegisterModelRequest, "id">>;
