export type NodeType = "mac" | "nvidia" | "other";

export interface Node {
  id: string;
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag: string | null;
  /**
   * Derived by the coordinator: the operator allows it **and** it was seen
   * within the liveness TTL. PRM-206 stopped showing only this, because it
   * collapses two facts the backend deliberately reports separately —
   * `fleet.py`: *"'cordoned' and 'not answering' need different actions from an
   * operator and a single boolean cannot tell them apart."*
   */
  is_active: boolean;
  /** The operator's half: false means cordoned on purpose. Was in the response
   * and declared nowhere, so the UI could not show it. */
  enabled: boolean;
  /** The observed half: when the coordinator last heard from this node. Null
   * means never. Nodes heartbeat every 10s against a 60s TTL. */
  last_seen_at: string | null;
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
