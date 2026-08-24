import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { InstancesResponse, NodesResponse } from "../types/api";
import type { InstanceActionResult, InstanceEntry, RegisterModelRequest } from "../types/instance";

const INSTANCES_KEY = ["instances"] as const;
const NODES_KEY = ["nodes"] as const;
const POLL_INTERVAL_MS = 5000;

export function useInstances() {
  return useQuery({
    queryKey: INSTANCES_KEY,
    queryFn: async () => (await apiClient.get<InstancesResponse>("/instances")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

export function useNodes() {
  return useQuery({
    queryKey: NODES_KEY,
    queryFn: async () => (await apiClient.get<NodesResponse>("/nodes")).data,
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

export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: InstanceActionArgs) => {
      await apiClient.delete(`/nodes/${node}/models/${modelId}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY }),
  });
}
