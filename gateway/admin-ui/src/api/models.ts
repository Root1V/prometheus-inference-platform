import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import { INSTANCES_KEY } from "./instances";
import type { Modality } from "../types/instance";
import type {
  DownloadEntry,
  HfFile,
  HfModelCard,
  HfSearchResult,
  ModelCatalogEntry,
  ModelsConfig,
  ModelSort,
  StartDownloadRequest,
  StartDownloadResult,
  UpdateModelsConfigRequest,
} from "../types/models";

const DOWNLOADS_POLL_MS = 2000;
const CATALOG_POLL_MS = 5000;

/** RM-51: the model catalog — GET /admin/api/models, mirrors useInstances()'s
 * cross-node aggregation. A downloaded model with zero instances only shows
 * up here, never in useInstances(). */
export const CATALOG_KEY = ["model-catalog"] as const;

export function useModelCatalog() {
  return useQuery({
    queryKey: CATALOG_KEY,
    queryFn: async () =>
      (
        await apiClient.get<{ models: ModelCatalogEntry[]; unreachable_nodes: string[] }>(
          "/models",
        )
      ).data,
    refetchInterval: CATALOG_POLL_MS,
  });
}

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
      queryClient.invalidateQueries({ queryKey: CATALOG_KEY });
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

function useDownloadAction(action: "cancel" | "pause" | "resume" | "retry") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, modelId }: { node: string; modelId: string }) =>
      (await apiClient.post(`/nodes/${node}/models/downloads/${modelId}/${action}`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["downloads"] }),
  });
}

export const useCancelDownload = () => useDownloadAction("cancel");
export const usePauseDownload = () => useDownloadAction("pause");
export const useResumeDownload = () => useDownloadAction("resume");
export const useRetryDownload = () => useDownloadAction("retry");

const MODELS_CONFIG_KEY = ["models-config"] as const;

export function useModelsConfig(node: string) {
  return useQuery({
    queryKey: [...MODELS_CONFIG_KEY, node] as const,
    queryFn: async () => (await apiClient.get<ModelsConfig>(`/nodes/${node}/models/config`)).data,
    enabled: node.length > 0,
  });
}

export function useUpdateModelsConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ node, data }: { node: string; data: UpdateModelsConfigRequest }) =>
      (await apiClient.patch<ModelsConfig>(`/nodes/${node}/models/config`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: MODELS_CONFIG_KEY }),
  });
}

export function useDeleteDownloadedModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      node,
      modelId,
      confirm,
    }: {
      node: string;
      modelId: string;
      /** RM-51: required when the model has running instances — otherwise
       * the request 400s listing which ones, instead of cascading silently. */
      confirm?: boolean;
    }) => {
      await apiClient.delete(`/nodes/${node}/models/${modelId}/downloaded`, {
        params: confirm ? { confirm: true } : undefined,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
      queryClient.invalidateQueries({ queryKey: ["downloads"] });
      queryClient.invalidateQueries({ queryKey: CATALOG_KEY });
    },
  });
}

/** PRM-109: edit the model itself — not one of its instances.
 *
 * Modality is a property of the weights, so every replica inherits one answer.
 * It used to be editable per instance, which meant two replicas of the same
 * model could route differently and correcting a mistake meant finding every
 * one of them. `PATCH /v1/backends/{id}` now refuses the field outright. */
export function useUpdateCatalogEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      node,
      modelId,
      data,
    }: {
      node: string;
      modelId: string;
      data: { name?: string; modality?: Modality };
    }) => (await apiClient.patch<ModelCatalogEntry>(`/nodes/${node}/catalog/${modelId}`, data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CATALOG_KEY });
      queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
    },
  });
}
