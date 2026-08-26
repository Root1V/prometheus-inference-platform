import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { CreateNodeRequest, Node, UpdateNodeRequest } from "../types/node";

const NODES_KEY = ["nodes-registry"] as const;
const POLL_INTERVAL_MS = 5000;

export function useNodeRegistry() {
  return useQuery({
    queryKey: NODES_KEY,
    queryFn: async () => (await apiClient.get<Node[]>("/nodes")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

export function useCreateNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateNodeRequest) => (await apiClient.post<Node>("/nodes", data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NODES_KEY }),
  });
}

export function useUpdateNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdateNodeRequest }) =>
      (await apiClient.patch<Node>(`/nodes/${id}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NODES_KEY }),
  });
}

export function useDeleteNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/nodes/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NODES_KEY }),
  });
}

export function useCheckNode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<Node>(`/nodes/${id}/check`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NODES_KEY }),
  });
}
