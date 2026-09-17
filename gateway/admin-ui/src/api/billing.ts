import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  BillingAlertsResponse,
  BillingHistory,
  BillingPeriodSummary,
  ClientBillingSettings,
  CurrencyRates,
  ModelPricesResponse,
  UpdateClientBillingSettingsRequest,
  UpdateModelPriceRequest,
} from "../types/billing";
import { apiClient } from "./client";

const BILLING_SETTINGS_KEY = "billing-settings";
const CURRENCY_RATES_KEY = ["currency-rates"] as const;
const BILLING_SUMMARY_KEY = "billing-summary";
const BILLING_HISTORY_KEY = "billing-history";
const BILLING_ALERTS_KEY = ["billing-alerts"] as const;
const MODEL_PRICES_KEY = ["model-prices"] as const;

/** Polled at the same cadence as useUsage()'s live "today" view. */
const ALERTS_POLL_MS = 15000;

export function useClientBillingSettings(clientId: string) {
  return useQuery({
    queryKey: [BILLING_SETTINGS_KEY, clientId] as const,
    queryFn: async () =>
      (await apiClient.get<ClientBillingSettings>(`/billing/clients/${clientId}/settings`)).data,
    enabled: clientId.length > 0,
  });
}

export function useUpdateClientBillingSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      clientId,
      data,
    }: {
      clientId: string;
      data: UpdateClientBillingSettingsRequest;
    }) =>
      (
        await apiClient.put<ClientBillingSettings>(`/billing/clients/${clientId}/settings`, data)
      ).data,
    onSuccess: (_data, { clientId }) => {
      queryClient.invalidateQueries({ queryKey: [BILLING_SETTINGS_KEY, clientId] });
      queryClient.invalidateQueries({ queryKey: [BILLING_SUMMARY_KEY, clientId] });
      queryClient.invalidateQueries({ queryKey: BILLING_ALERTS_KEY });
    },
  });
}

export function useCurrencyRates() {
  return useQuery({
    queryKey: CURRENCY_RATES_KEY,
    queryFn: async () => (await apiClient.get<CurrencyRates>("/billing/currency-rates")).data,
  });
}

export function useUpdateCurrencyRates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (rates: CurrencyRates) =>
      (await apiClient.put<CurrencyRates>("/billing/currency-rates", rates)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CURRENCY_RATES_KEY }),
  });
}

/** @param period Optional "YYYY-MM"; defaults to the current UTC month server-side. */
export function useBillingSummary(clientId: string, period?: string) {
  return useQuery({
    queryKey: [BILLING_SUMMARY_KEY, clientId, period ?? "current"] as const,
    queryFn: async () =>
      (
        await apiClient.get<BillingPeriodSummary>(`/billing/clients/${clientId}/summary`, {
          params: period ? { period } : undefined,
        })
      ).data,
    enabled: clientId.length > 0,
  });
}

export function useBillingHistory(clientId: string, periods = 6) {
  return useQuery({
    queryKey: [BILLING_HISTORY_KEY, clientId, periods] as const,
    queryFn: async () =>
      (
        await apiClient.get<BillingHistory>(`/billing/clients/${clientId}/history`, {
          params: { periods },
        })
      ).data,
    enabled: clientId.length > 0,
  });
}

/** Feeds the in-app budget alert banner — clients with a currently-crossed threshold. */
export function useBillingAlerts() {
  return useQuery({
    queryKey: BILLING_ALERTS_KEY,
    queryFn: async () => (await apiClient.get<BillingAlertsResponse>("/billing/alerts")).data,
    refetchInterval: ALERTS_POLL_MS,
  });
}

/**
 * Admin-configurable model pricing — replaces hand-editing pricing.yaml +
 * restarting the gateway. A saved price applies to the very next request.
 */
export function useModelPrices() {
  return useQuery({
    queryKey: MODEL_PRICES_KEY,
    queryFn: async () => (await apiClient.get<ModelPricesResponse>("/billing/pricing")).data,
  });
}

export function useUpdateModelPrice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ modelId, data }: { modelId: string; data: UpdateModelPriceRequest }) =>
      (await apiClient.put(`/billing/pricing/${modelId}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: MODEL_PRICES_KEY }),
  });
}

// PRM-125 removed useDeleteModelPrice: the reset button fills the inputs now
// and Save is what writes, so nothing in the dashboard deletes a price. The
// DELETE endpoint still exists and still restores the modality's base price
// (PRM-124) — an API caller asking for it explicitly is a different thing from
// a toolbar button doing it on a click.
