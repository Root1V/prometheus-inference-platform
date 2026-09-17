import { useQuery } from "@tanstack/react-query";
import { rootClient } from "./client";

export interface ModelUsageEntry {
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  /** null when this model has no configured price (docs/roadmap.md RM-33) — never $0. */
  estimated_cost_usd: number | null;
  /** RM-60 follow-up: estimated_cost_usd broken into what's paid for input
   * tokens, inference (completion) tokens, and images — each independently
   * null (never $0) when unpriced for that component. */
  prompt_cost_usd: number | null;
  completion_cost_usd: number | null;
  image_cost_usd: number | null;
}

export interface UsageEntry {
  client_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  estimated_cost_usd: number | null;
  prompt_cost_usd: number | null;
  completion_cost_usd: number | null;
  image_cost_usd: number | null;
  by_model: ModelUsageEntry[];
}

/** Gateway's GET /v1/usage — per-client token totals (+ per-model breakdown) for one UTC day. */
export interface UsageResponse {
  object: string;
  window: string;
  data: UsageEntry[];
}

const USAGE_KEY = "gateway-usage";
const POLL_INTERVAL_MS = 15000;

/** @param date Optional YYYY-MM-DD (UTC) day to query; defaults to today server-side. */
export function useUsage(date?: string) {
  return useQuery({
    queryKey: [USAGE_KEY, date ?? "today"] as const,
    // Root-level path, not under /admin/api — same-origin, requires admin:read.
    queryFn: async () =>
      (
        await rootClient.get<UsageResponse>("/v1/usage", {
          params: date ? { date } : undefined,
        })
      ).data,
    // Only poll the live "today" view — a past date is immutable, no need to refetch it.
    refetchInterval: date ? false : POLL_INTERVAL_MS,
  });
}

/**
 * RM-60: GET /v1/usage/export as a client-side file download. A plain
 * `<a href>` navigation would 401 — the Bearer token lives in sessionStorage
 * and is only attached by rootClient's axios interceptor — so this fetches
 * through rootClient and triggers the download via a Blob + synthetic click.
 */
export async function downloadUsageExportCsv(params: {
  start: string;
  end: string;
  client_id?: string;
}): Promise<void> {
  const response = await rootClient.get<string>("/v1/usage/export", {
    params,
    responseType: "text",
  });
  const blob = new Blob([response.data], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `usage-${params.start}-to-${params.end}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** One data row of GET /v1/usage/export — mirrors its CSV columns (the
 * generated_at/period_start/period_end/client_id columns are omitted since
 * they're constant across every row of a single export and shown once by
 * the caller instead). Excludes the CSV's own trailing TOTAL row. */
export interface UsageExportRow {
  recorded_at: string;
  model_id: string;
  /** PRM-119: the name the model answered to when the row was written. The
   *  export's own comment says this is "what an invoice should show", and the
   *  invoice was showing `model_id` — the catalog id, which a person does not
   *  recognise and which PRM-115 had just finished removing from the one other
   *  place it faced a reader. */
  model_slug: string;
  request_kind: string;
  prompt_tokens: number;
  completion_tokens: number;
  image_count: number;
  prompt_price_per_1m: number | null;
  completion_price_per_1m: number | null;
  image_price_each: number | null;
  cost_usd: number | null;
}

/** Minimal RFC 4180 field splitter — handles double-quoted fields (with ""
 * as an escaped quote), which is all Python's csv.writer ever produces for
 * a field containing a comma/quote/newline. Good enough for this fixed,
 * backend-generated schema; not a general-purpose CSV parser. */
function splitCsvLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        current += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      fields.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields;
}

function parseNullableNumber(raw: string | undefined): number | null {
  return raw === undefined || raw === "" ? null : Number(raw);
}

/**
 * Same data as downloadUsageExportCsv, but parsed for inline display instead
 * of triggering a file download — used by Billing's Period History "expand"
 * row, per the user's request to see the CSV's own detail directly on the
 * page without having to download it.
 */
export function useUsageExportRows(
  params: { start: string; end: string; client_id?: string },
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["usage-export-rows", params.start, params.end, params.client_id] as const,
    queryFn: () => fetchUsageExportRows(params),
    enabled,
  });
}

async function fetchUsageExportRows(params: {
  start: string;
  end: string;
  client_id?: string;
}): Promise<UsageExportRow[]> {
  const response = await rootClient.get<string>("/v1/usage/export", {
    params,
    responseType: "text",
  });
  const lines = response.data.trim().split("\n");
  const rows: UsageExportRow[] = [];
  if (lines.length === 0) return rows;
  // PRM-119: read by column NAME, not position. A-13 made "new columns are
  // appended, existing ones never move" a written contract precisely so
  // position-reading consumers keep working — but reading by position also
  // means a column appended later is invisible, which is how `model_slug`
  // (appended by PRM-113, and the one an invoice should show) never reached
  // this table. By name, a future column costs nothing to pick up.
  const header = splitCsvLine(lines[0]);
  const at = (name: string) => header.indexOf(name);
  const col = {
    recorded_at: at("recorded_at"),
    model_id: at("model_id"),
    model_slug: at("model_slug"),
    request_kind: at("request_kind"),
    prompt_tokens: at("prompt_tokens"),
    completion_tokens: at("completion_tokens"),
    image_count: at("image_count"),
    prompt_price_per_1m: at("prompt_price_per_1m"),
    completion_price_per_1m: at("completion_price_per_1m"),
    image_price_each: at("image_price_each"),
    cost_usd: at("cost_usd"),
  };
  for (const line of lines.slice(1)) {
    if (!line) continue;
    const f = splitCsvLine(line);
    const modelId = f[col.model_id];
    if (modelId === "TOTAL") continue;
    rows.push({
      recorded_at: f[col.recorded_at],
      model_id: modelId,
      // An export predating PRM-113 has no such column; fall back rather than
      // render "undefined" at a reader.
      model_slug: (col.model_slug >= 0 ? f[col.model_slug] : "") || modelId,
      request_kind: f[col.request_kind],
      prompt_tokens: Number(f[col.prompt_tokens]) || 0,
      completion_tokens: Number(f[col.completion_tokens]) || 0,
      image_count: Number(f[col.image_count]) || 0,
      prompt_price_per_1m: parseNullableNumber(f[col.prompt_price_per_1m]),
      completion_price_per_1m: parseNullableNumber(f[col.completion_price_per_1m]),
      image_price_each: parseNullableNumber(f[col.image_price_each]),
      cost_usd: parseNullableNumber(f[col.cost_usd]),
    });
  }
  return rows;
}
