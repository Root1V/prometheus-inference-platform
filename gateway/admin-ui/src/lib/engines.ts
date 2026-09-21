import type { Backend } from "../types/instance";

/**
 * PRM-133: the engines this platform knows how to launch — the UI's mirror of
 * manager-core's `BACKENDS`.
 *
 * It lived in two components, copied. That was harmless while both showed the
 * whole list unconditionally; it stops being harmless now that a node's own
 * declaration has to be checked against it, because a list that exists twice
 * gets filtered once.
 */
export const ENGINES: Backend[] = ["llama_cpp", "mlx", "vllm", "sglang", "sd_cpp"];

/** Display labels — the stored ids are snake_case and the products are not. */
export const ENGINE_LABELS: Record<Backend, string> = {
  llama_cpp: "llama.cpp",
  mlx: "MLX",
  vllm: "vLLM",
  sglang: "SGLang",
  sd_cpp: "stable-diffusion.cpp",
};

/**
 * What a node can actually be asked to run.
 *
 * Three states in, three answers out:
 *   - `null` (never declared) → every engine, which is exactly what the form
 *     did before this existed. An undeclared node must not become unusable.
 *   - `[]` (declared none) → nothing. The caller has to render that as a
 *     reason, not as an empty dropdown.
 *   - a list → its intersection with ENGINES, in ENGINES' order.
 *
 * The intersection is why auth-service does not need to know what an engine
 * is: a name it stored that this build has never heard of cannot become a
 * selectable option.
 */
export function enginesAvailableOn(declared: string[] | null | undefined): Backend[] {
  if (declared === null || declared === undefined) return ENGINES;
  return ENGINES.filter((e) => declared.includes(e));
}
