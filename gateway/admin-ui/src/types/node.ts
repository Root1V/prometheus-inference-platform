export type NodeType = "mac" | "nvidia" | "other";

export interface Node {
  id: string;
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface CreateNodeRequest {
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag?: string;
}

/** PATCH /admin/api/nodes/{id} — `name` is immutable. */
export interface UpdateNodeRequest {
  manager_url?: string;
  node_type?: NodeType;
  tag?: string | null;
}
