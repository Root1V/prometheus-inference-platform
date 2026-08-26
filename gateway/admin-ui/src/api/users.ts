import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  CreatePrincipalRequest,
  CreatePrincipalResponse,
  Principal,
  ReactivateResponse,
  ResetPasswordResponse,
  RotateSecretResponse,
  ShareLinkResponse,
  UpdatePrincipalRequest,
} from "../types/user";

const USERS_KEY = ["users"] as const;
const POLL_INTERVAL_MS = 5000;

export function useUsers() {
  return useQuery({
    queryKey: USERS_KEY,
    queryFn: async () => (await apiClient.get<Principal[]>("/users")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreatePrincipalRequest) =>
      (await apiClient.post<CreatePrincipalResponse>("/users", data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clientId, data }: { clientId: string; data: UpdatePrincipalRequest }) =>
      (await apiClient.patch<Principal>(`/users/${clientId}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useDeactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) => {
      await apiClient.delete(`/users/${clientId}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useReactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (await apiClient.post<ReactivateResponse>(`/users/${clientId}/reactivate`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useRotateSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (await apiClient.post<RotateSecretResponse>(`/users/${clientId}/rotate-secret`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useResetPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (await apiClient.post<ResetPasswordResponse>(`/users/${clientId}/reset-password`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useGenerateShareLink() {
  return useMutation({
    mutationFn: async ({ clientId, secret }: { clientId: string; secret: string }) =>
      (await apiClient.post<ShareLinkResponse>(`/users/${clientId}/share`, { secret })).data,
  });
}
