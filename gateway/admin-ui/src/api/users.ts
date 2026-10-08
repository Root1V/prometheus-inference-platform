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

/**
 * The principal list — PRM-240.
 *
 * Polling is opt-in now. This hook was written for the Users page, where a
 * stale list is a real problem: you create a client and want to see it. Six
 * other pages then imported it **to turn a client_id into a name** and
 * inherited the five-second refetch with it. Measured from the browser on the
 * Activity page: 165 calls to `/admin/api/users` in 15 minutes to paint three
 * names that had not changed — about twelve requests a minute, per tab,
 * against the admin bucket that `MIN_ADMIN_RPM` exists to protect.
 *
 * One query key either way, so the list is fetched once and shared; `live`
 * only decides whether this caller keeps asking. A mutation still
 * invalidates the key, so a page showing names updates the moment a name
 * changes on the page that changed it.
 */
export function useUsers(options: { live?: boolean } = {}) {
  return useQuery({
    queryKey: USERS_KEY,
    queryFn: async () => (await apiClient.get<Principal[]>("/users")).data,
    refetchInterval: options.live ? POLL_INTERVAL_MS : false,
    // Names change when someone changes them, and that invalidates the key.
    // Five minutes is how long a tab that never navigates waits to notice a
    // change made in another tab.
    staleTime: options.live ? 0 : 5 * 60_000,
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
    mutationFn: async ({
      clientId,
      data,
    }: {
      clientId: string;
      data: UpdatePrincipalRequest;
    }) => (await apiClient.patch<Principal>(`/users/${clientId}`, data)).data,
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

/** Permanent hard-delete (RM-27) — distinct from useDeactivateUser's reversible revoke. */
export function useDeleteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) => {
      await apiClient.delete(`/users/${clientId}`, {
        params: { permanent: true },
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useReactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (
        await apiClient.post<ReactivateResponse>(
          `/users/${clientId}/reactivate`,
        )
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useRotateSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (
        await apiClient.post<RotateSecretResponse>(
          `/users/${clientId}/rotate-secret`,
        )
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useResetPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: string) =>
      (
        await apiClient.post<ResetPasswordResponse>(
          `/users/${clientId}/reset-password`,
        )
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });
}

export function useGenerateShareLink() {
  return useMutation({
    mutationFn: async ({
      clientId,
      secret,
    }: {
      clientId: string;
      secret: string;
    }) =>
      (
        await apiClient.post<ShareLinkResponse>(`/users/${clientId}/share`, {
          secret,
        })
      ).data,
  });
}
