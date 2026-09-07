import type { CurrencyCode } from "../lib/format";

/** GET/PUT /admin/api/billing/clients/{client_id}/settings */
export interface ClientBillingSettings {
  client_id: string;
  /** null = no hard cap enforced for this client. */
  monthly_spend_cap_usd: number | null;
  /** Comma-separated percentages, e.g. "50,80,100"; null = platform default. */
  alert_thresholds_percent: string | null;
  tax_rate_percent: number;
  preferred_currency: CurrencyCode;
}

export type UpdateClientBillingSettingsRequest = Omit<ClientBillingSettings, "client_id">;

/** GET/PUT /admin/api/billing/currency-rates — currency_code -> units per 1 USD. */
export type CurrencyRates = Record<string, number>;

export interface DailyCostEntry {
  day: string;
  cost_usd: number | null;
  tokens: number;
  request_count: number;
}

export interface ModelCostEntry {
  model_id: string;
  cost_usd: number | null;
  tokens: number;
  request_count: number;
}

/** GET /admin/api/billing/clients/{client_id}/summary */
export interface BillingPeriodSummary {
  client_id: string;
  period: string;
  period_start: string;
  period_end: string;
  subtotal_usd: number;
  tax_rate_percent: number;
  tax_amount_usd: number;
  total_usd: number;
  preferred_currency: CurrencyCode;
  total_in_preferred_currency: number;
  exchange_rate_used: number;
  total_tokens: number;
  request_count: number;
  monthly_spend_cap_usd: number | null;
  daily: DailyCostEntry[];
  by_model: ModelCostEntry[];
}

/** GET /admin/api/billing/clients/{client_id}/history */
export interface BillingHistory {
  client_id: string;
  periods: BillingPeriodSummary[];
}

/** One row of GET /admin/api/billing/alerts. */
export interface BillingAlert {
  client_id: string;
  monthly_spend_cap_usd: number | null;
  spend_usd: number;
  crossed_thresholds_percent: number[];
}

export interface BillingAlertsResponse {
  object: string;
  data: BillingAlert[];
}

/** One entry of GET /admin/api/billing/pricing — keyed by model_id. */
export interface ModelPriceEntry {
  prompt_price_per_1m: number | null;
  completion_price_per_1m: number | null;
  image_price: number | null;
  /** "db" = admin-configured (this dashboard); "file" = from pricing.yaml, read-only until edited. */
  source: "db" | "file";
}

export type ModelPrices = Record<string, ModelPriceEntry>;

export interface ModelPricesResponse {
  object: string;
  data: ModelPrices;
}

export interface UpdateModelPriceRequest {
  prompt_price_per_1m: number | null;
  completion_price_per_1m: number | null;
  image_price: number | null;
}
