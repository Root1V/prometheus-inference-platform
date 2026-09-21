export type NodeType = "mac" | "nvidia" | "other";

export interface Node {
  id: string;
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag: string | null;
  is_active: boolean;
  /** RM-62: the two $/hour components the operator enters — nothing in this
   * codebase can derive them. Always a real number: left blank at creation,
   * each falls back to a platform default (see CreateNodeModal's placeholders). */
  hardware_amortization_usd_per_hour: number;
  electricity_usd_per_hour: number;
  /** Margin applied by the Model Pricing table's "suggest price" calculator. */
  price_margin_multiplier: number;
  /** Computed = hardware_amortization_usd_per_hour + electricity_usd_per_hour. */
  hourly_cost_usd: number;
  /** PRM-133: the inference engines installed on this node.
   * `null` means never declared — NOT "none". Read it through
   * `enginesAvailableOn()` in lib/engines.ts rather than testing it here. */
  engines: string[] | null;
  created_at: string;
  updated_at: string | null;
}

export interface CreateNodeRequest {
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag?: string;
  hardware_amortization_usd_per_hour?: number;
  electricity_usd_per_hour?: number;
  price_margin_multiplier?: number;
  engines?: string[];
}

/** PATCH /admin/api/nodes/{id} — `name` is immutable. */
export interface UpdateNodeRequest {
  manager_url?: string;
  node_type?: NodeType;
  tag?: string | null;
  hardware_amortization_usd_per_hour?: number;
  electricity_usd_per_hour?: number;
  price_margin_multiplier?: number;
  /** PRM-133: `[]` is a real edit (declared none); omit to leave alone. */
  engines?: string[];
}
