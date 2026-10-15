export type NodeType = "mac" | "nvidia" | "other";

/** What can do matrix maths on a node, if anything — mirrors `Accelerator` in
 * manager-core's profile.py. `kind` is `"none"` when the node looked and found
 * nothing, which is a different answer from not having looked. */
export interface Accelerator {
  kind: string;
  name: string | null;
  /** Null on unified-memory machines rather than a copy of system RAM: the
   * same number under two names is the kind of thing that later disagrees
   * with itself. */
  memory_mb: number | null;
  unified_memory: boolean;
}

/** Mirrors `DeviceProfile` in manager-core's profile.py. Every field is
 * optional because the probe never raises — a half-answer beats an exception
 * when the caller is a heartbeat. */
export interface DeviceProfile {
  os: string;
  os_release: string;
  arch: string;
  cpu_model: string | null;
  cpu_cores_physical: number | null;
  cpu_cores_logical: number | null;
  ram_total_mb: number | null;
  ram_available_mb: number | null;
  disk_total_mb: number | null;
  disk_free_mb: number | null;
  /** What was actually measured. The node aims at its models directory and
   * falls back to the volume anchor when that does not exist yet, so this
   * names the reading rather than implying one. */
  disk_path: string | null;
  accelerator: Accelerator | null;
  /** Which probes failed, named. An empty profile with no explanation is the
   * shape that gets read as "this machine has nothing". */
  probe_errors: string[];
}

/** One engine on a node, as the node itself found it — PRM-264. Mirrors
 * `EngineStatus` in manager-core's engines.py. `reason` is always set when
 * `installed` is false: a binary missing from PATH and a configured path that
 * does not exist have different fixes. */
export interface EngineStatus {
  backend: string;
  installed: boolean;
  /** The command that was checked — what the manager would execute. */
  binary: string;
  /** Where it resolved to, when it did. An absolute path tells a system
   * install from one inside a virtualenv, which is the usual surprise. */
  path: string | null;
  reason: string | null;
}

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
  /** PRM-263: what the node read about its own machine, sent with the
   * heartbeat. `null` means it has never reported one — NOT "this machine has
   * nothing", which is the reading `probe_errors` exists to keep separable. */
  profile: DeviceProfile | null;
  /** When that reading was taken. The agent caches it for 5 minutes and a node
   * that stopped reporting leaves its last profile behind looking current, so
   * the profile is never shown without this. */
  profile_seen_at: string | null;
  /** PRM-133: the inference engines installed on this node.
   * `null` means never declared — NOT "none". Read it through
   * `enginesAvailableOn()` in lib/engines.ts rather than testing it here. */
  engines: string[] | null;
  /** PRM-264: what the node found installed, one entry per known backend.
   * `null` means it has never reported — not "it has none", which is `[]`.
   * Kept beside `engines` rather than replacing it: that one is the operator's
   * intent and this is an observation, and PRM-151 is what happens when the
   * two share a field. */
  engines_detected: EngineStatus[] | null;
  engines_seen_at: string | null;
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
