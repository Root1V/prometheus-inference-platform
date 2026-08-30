import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import { INSTANCES_KEY } from "./instances";
import type {
  DownloadEntry,
  HfFile,
  HfModelCard,
  HfSearchResult,
  ModelSort,
  StartDownloadRequest,
  StartDownloadResult,
} from "../types/models";

const DOWNLOADS_POLL_MS = 2000;

/** Not auto-fetched — the caller passes the current search box value and
 * only enables the query once there's a non-empty term to search for. */
export function useModelSearch(node: string, query: string, sort: ModelSort | "" = "") {
  return useQuery({
    queryKey: ["model-search", node, query, sort] as const,
    queryFn: async () =>
      (
        await apiClient.get<{ results: HfSearchResult[] }>(`/nodes/${node}/models/search`, {
          params: { q: query, ...(sort ? { sort } : {}) },
        })
      ).data.results,
    enabled: node.length > 0 && query.trim().length > 0,
  });
}

export function useModelFiles(node: string, repoId: string) {
  return useQuery({
    queryKey: ["model-files", node, repoId] as const,
    queryFn: async () =>
      (
        await apiClient.get<{ files: HfFile[] }>(`/nodes/${node}/models/search/files`, {
          params: { repo_id: repoId },
        })
      ).data.files,
    enabled: node.length > 0 && repoId.length > 0,
  });
}

export function useModelCard(node: string, repoId: string) {
  return useQuery({
    queryKey: ["model-card", node, repoId] as const,
    queryFn: async () =>
      (await apiClient.get<HfModelCard>(`/nodes/${node}/models/search/card`, { params: { repo_id: repoId } })).data,
    enabled: node.length > 0 && repoId.length > 0,
  });
}

export function useStartDownload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, data }: { node: string; data: StartDownloadRequest }) =>
      (await apiClient.post<StartDownloadResult>(`/nodes/${node}/models/downloads`, data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
      queryClient.invalidateQueries({ queryKey: ["downloads"] });
    },
  });
}

/** Polls continuously while the Models page is mounted — downloads are rare
 * enough (an operator-triggered action, not constant background traffic)
 * that a flat poll is simpler than gating on "is anything active" client-side. */
export function useDownloads(node: string) {
  return useQuery({
    queryKey: ["downloads", node] as const,
    queryFn: async () =>
      (await apiClient.get<{ downloads: DownloadEntry[] }>(`/nodes/${node}/models/downloads`)).data.downloads,
    enabled: node.length > 0,
    refetchInterval: DOWNLOADS_POLL_MS,
  });
}

function useDownloadAction(action: "cancel" | "retry") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: { node: string; modelId: string }) =>
      (await apiClient.post(`/nodes/${node}/models/downloads/${modelId}/${action}`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["downloads"] }),
  });
}

export const useCancelDownload = () => useDownloadAction("cancel");
export const useRetryDownload = () => useDownloadAction("retry");

export function useDeleteDownloadedModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: { node: string; modelId: string }) => {
      await apiClient.delete(`/nodes/${node}/models/${modelId}/downloaded`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
      queryClient.invalidateQueries({ queryKey: ["downloads"] });
    },
  });
}
