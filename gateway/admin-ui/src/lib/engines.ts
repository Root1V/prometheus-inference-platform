import type { Backend } from "../types/instance";
import type { Node } from "../types/node";

/**
 * PRM-133: the engines this platform knows how to launch — the UI's mirror of
 * manager-core's `BACKENDS`.
 *
 * It lived in two components, copied. That was harmless while both showed the
 * whole list unconditionally; it stops being harmless now that a node's own
 * declaration has to be checked against it, because a list that exists twice
 * gets filtered once.
 */
export const ENGINES: Backend[] = [
  "llama_cpp",
  "mlx",
  "vllm",
  "sglang",
  "sd_cpp",
  "hf_serve",
  "tei",
  "laya",
];

/** Display labels — the stored ids are snake_case and the products are not. */
export const ENGINE_LABELS: Record<Backend, string> = {
  llama_cpp: "llama.cpp",
  mlx: "MLX",
  vllm: "vLLM",
  sglang: "SGLang",
  sd_cpp: "stable-diffusion.cpp",
  hf_serve: "hf-serve",
  tei: "TEI",
  laya: "Laya",
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

/** PRM-264. Declared engines are the operator's intent and detected ones are
 * the node's own observation; they are stored apart (PRM-151 is what happens
 * when two such facts share a field) and are worth most where they disagree.
 * Returns what was declared and not found — a node claiming an engine it does
 * not have is otherwise discovered by a failed start, at the moment somebody
 * wanted the model.
 *
 * Empty when either side is unknown: a node that never scanned itself is not
 * evidence against anything the operator said.
 */
export function engineDisagreement(node: Node): string[] {
  if (node.engines === null || node.engines_detected === null) return [];
  const present = new Set(node.engines_detected.filter((e) => e.installed).map((e) => e.backend));
  return node.engines.filter((declared) => !present.has(declared));
}

/** The display name for a backend, falling back to its raw id rather than
 * rendering `undefined` for one this UI has not learned yet. */
export function engineLabel(backend: string): string {
  return ENGINE_LABELS[backend as Backend] ?? backend;
}
