import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { InstancesResponse } from "../types/api";
import type { Node } from "../types/node";
import type {
  InstanceActionResult,
  InstanceEntry,
  RegisterModelRequest,
  UpdateModelRequest,
} from "../types/instance";

export const INSTANCES_KEY = ["instances"] as const;
const NODE_NAMES_KEY = ["node-names"] as const;
const POLL_INTERVAL_MS = 5000;

export function useInstances() {
  return useQuery({
    queryKey: INSTANCES_KEY,
    queryFn: async () => (await apiClient.get<InstancesResponse>("/instances")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

/** The nodes, for the instance-creation pickers (RM-20 full CRUD lives in
 * api/nodes.ts's useNodeRegistry, used by the Nodes admin page).
 *
 * PRM-133: was `.map((n) => n.name)`. The engine picker needs the node's
 * declared engine list, and that list is already in this response — throwing
 * the rest away here meant a second request for data we had. */
export function useNodes() {
  return useQuery({
    queryKey: NODE_NAMES_KEY,
    queryFn: async () => (await apiClient.get<Node[]>("/nodes")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

interface InstanceActionArgs {
  node: string;
  modelId: string;
}

function useInstanceAction(action: "start" | "stop" | "restart") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: InstanceActionArgs) =>
      (await apiClient.post<InstanceActionResult>(`/nodes/${node}/instances/${modelId}/${action}`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }),
  });
}

export const useStartInstance = () => useInstanceAction("start");
export const useStopInstance = () => useInstanceAction("stop");
export const useRestartInstance = () => useInstanceAction("restart");

export function useRegisterModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, data }: { node: string; data: RegisterModelRequest }) =>
      (await apiClient.post<InstanceEntry>(`/nodes/${node}/models`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }),
  });
}

export function useUpdateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      node,
      modelId,
      data,
    }: {
      node: string;
      modelId: string;
      data: UpdateModelRequest;
    }) => (await apiClient.patch<InstanceEntry>(`/nodes/${node}/models/${modelId}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }),
  });
}

interface InstanceLogsResponse {
  model_id: string;
  lines: string[];
}

/** RM-13: tails an instance's log file. Only polls while `enabled` (the row is expanded)
 * — otherwise this would multiply request volume by the number of registered models. */
export function useInstanceLogs(node: string, modelId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["instance-logs", node, modelId] as const,
    queryFn: async () =>
      (
        await apiClient.get<InstanceLogsResponse>(`/nodes/${node}/instances/${modelId}/logs`, {
          params: { tail: 200 },
        })
      ).data,
    enabled,
    refetchInterval: enabled ? 3000 : false,
  });
}

export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: InstanceActionArgs) => {
      await apiClient.delete(`/nodes/${node}/models/${modelId}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }),
  });
}
