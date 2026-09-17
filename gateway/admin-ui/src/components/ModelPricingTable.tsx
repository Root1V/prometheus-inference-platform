import { Calculator, RotateCcw, Save } from "lucide-react";
import { useState } from "react";
import type { BackendMetrics } from "../api/metrics";
import { useDeleteModelPrice, useModelPrices, useUpdateModelPrice } from "../api/billing";
import { useMetrics } from "../api/metrics";
import { useModelCatalog } from "../api/models";
import { useNodeRegistry } from "../api/nodes";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import type { ModelPriceEntry } from "../types/billing";

const inputClass =
  "w-24 rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-text focus:border-primary focus:outline-none";

function parseOptionalNumber(raw: string): number | null | "invalid" {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isNaN(n) ? "invalid" : n;
}

/** RM-62: break-even $/hour-of-node divided by real observed throughput,
 * scaled to the usual $/1M-tokens or $/image unit, then bumped by `margin`
 * (e.g. 1.3 = 30% over break-even) — never applied silently, only prefills
 * the editable inputs below for the operator to review before Save. */
function suggestPricePerUnit(
  hourlyCostUsd: number,
  unitsPerSecond: number,
  unitsPerPricedBatch: number,
  margin: number,
): number {
  return (hourlyCostUsd / (unitsPerSecond * 3600)) * unitsPerPricedBatch * margin;
}

function ModelPricingRow({
  modelId,
  entry,
  hourlyCostUsd,
  metrics,
  margin,
}: {
  modelId: string;
  entry: ModelPriceEntry | undefined;
  hourlyCostUsd: number | null;
  metrics: BackendMetrics | undefined;
  margin: number;
}) {
  const { showToast } = useToast();
  const updatePrice = useUpdateModelPrice();
  const deletePrice = useDeleteModelPrice();

  const [promptPrice, setPromptPrice] = useState(entry?.prompt_price_per_1m?.toString() ?? "");
  const [completionPrice, setCompletionPrice] = useState(
    entry?.completion_price_per_1m?.toString() ?? "",
  );
  const [imagePrice, setImagePrice] = useState(entry?.image_price?.toString() ?? "");

  const isBusy = updatePrice.isPending || deletePrice.isPending;
  const isDbOverride = entry?.source === "db";
  // PRM-120: a seeded base price is not a decision anybody made. Saying so is
  // the whole reason it is stored with a flag instead of looking like one.
  const isDefault = entry?.source === "default";

  function handleSuggest() {
    if (!hourlyCostUsd) {
      showToast(
        `No hourly cost configured for ${modelId}'s node — set one on the Nodes page first.`,
        "error",
      );
      return;
    }
    if (!metrics) {
      showToast(`No throughput data yet for ${modelId} — send it a real request first.`, "error");
      return;
    }
    let applied = false;
    if (metrics.tokens_per_second_avg) {
      setCompletionPrice(
        suggestPricePerUnit(hourlyCostUsd, metrics.tokens_per_second_avg, 1_000_000, margin).toFixed(4),
      );
      applied = true;
    }
    if (metrics.prompt_tokens_per_second_avg) {
      setPromptPrice(
        suggestPricePerUnit(
          hourlyCostUsd,
          metrics.prompt_tokens_per_second_avg,
          1_000_000,
          margin,
        ).toFixed(4),
      );
      applied = true;
    }
    if (metrics.images_per_second_avg) {
      setImagePrice(suggestPricePerUnit(hourlyCostUsd, metrics.images_per_second_avg, 1, margin).toFixed(4));
      applied = true;
    }
    if (!applied) {
      showToast(`No usable throughput samples yet for ${modelId}.`, "error");
      return;
    }
    showToast(`Suggested price filled in for ${modelId} — review, then Save.`, "success");
  }

  function handleSave() {
    const prompt = parseOptionalNumber(promptPrice);
    const completion = parseOptionalNumber(completionPrice);
    const image = parseOptionalNumber(imagePrice);
    if (prompt === "invalid" || completion === "invalid" || image === "invalid") {
      showToast("Prices must be numbers, or blank.", "error");
      return;
    }
    if ((prompt === null) !== (completion === null)) {
      showToast(
        "Prompt and completion price must both be set, or both left blank — a model can " +
          "still be priced per-image only.",
        "error",
      );
      return;
    }
    updatePrice.mutate(
      {
        modelId,
        data: {
          prompt_price_per_1m: prompt,
          completion_price_per_1m: completion,
          image_price: image,
        },
      },
      {
        onSuccess: () => showToast(`Price saved for ${modelId} — applies immediately`, "success"),
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  function handleReset() {
    deletePrice.mutate(modelId, {
      onSuccess: () => {
        showToast(`Override removed for ${modelId}`, "success");
        setPromptPrice("");
        setCompletionPrice("");
        setImagePrice("");
      },
      onError: (e) => showToast(getErrorMessage(e), "error"),
    });
  }

  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-4 py-2 font-medium text-text">
        {modelId}
        {entry && (
          <span
            title={
              isDefault
                ? "Base price for this modality, applied when the model was catalogued. " +
                  "Nobody has reviewed it — use the calculator once this model has served " +
                  "traffic, then Save."
                : undefined
            }
            className={
              "ml-2 rounded-full px-2 py-0.5 text-xs " +
              (isDbOverride
                ? "bg-primary/10 text-primary"
                : isDefault
                  ? "bg-yellow-500/10 text-yellow-800 dark:text-yellow-300"
                  : "bg-background text-text-muted")
            }
          >
            {isDbOverride ? "custom" : isDefault ? "base price" : "pricing.yaml"}
          </span>
        )}
      </td>
      <td className="px-4 py-2">
        <input
          value={promptPrice}
          onChange={(e) => setPromptPrice(e.target.value)}
          placeholder="—"
          inputMode="decimal"
          className={inputClass}
        />
      </td>
      <td className="px-4 py-2">
        <input
          value={completionPrice}
          onChange={(e) => setCompletionPrice(e.target.value)}
          placeholder="—"
          inputMode="decimal"
          className={inputClass}
        />
      </td>
      <td className="px-4 py-2">
        <input
          value={imagePrice}
          onChange={(e) => setImagePrice(e.target.value)}
          placeholder="—"
          inputMode="decimal"
          className={inputClass}
        />
      </td>
      <td className="px-4 py-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            title="Suggest a price from this model's node cost + observed throughput"
            aria-label={`Suggest price for ${modelId}`}
            onClick={handleSuggest}
            className="rounded-md p-1.5 text-text-muted hover:bg-primary/10 hover:text-primary"
          >
            <Calculator size={16} />
          </button>
          <button
            type="button"
            title="Save"
            aria-label={`Save price for ${modelId}`}
            disabled={isBusy}
            onClick={handleSave}
            className="rounded-md p-1.5 text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save size={16} />
          </button>
          {isDbOverride && (
            <button
              type="button"
              title="Remove override (falls back to pricing.yaml, if any)"
              aria-label={`Remove price override for ${modelId}`}
              disabled={isBusy}
              onClick={handleReset}
              className="rounded-md p-1.5 text-text-muted hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw size={16} />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

/**
 * RM-60 follow-up: lets an admin set/change per-model prices from the
 * dashboard instead of hand-editing gateway/pricing.yaml + restarting.
 * A saved price applies to the very next request (PricingTable.set_price
 * mutates the live in-memory table) and is persisted to the DB so it
 * survives a restart too.
 */
export function ModelPricingTable() {
  const pricesQuery = useModelPrices();
  const catalogQuery = useModelCatalog();
  const nodesQuery = useNodeRegistry();
  const metricsQuery = useMetrics();

  if (pricesQuery.isLoading || catalogQuery.isLoading) {
    return <div className="p-8 text-center text-text-muted">Loading…</div>;
  }

  const prices = pricesQuery.data?.data ?? {};
  const catalogEntries = catalogQuery.data?.models ?? [];
  const catalogIds = catalogEntries.map((m) => m.id);
  // Every priced model (yaml or db) plus every catalog model not yet priced —
  // a model can be priced before it's downloaded, but usually it's the
  // other way around.
  const modelIds = Array.from(new Set([...Object.keys(prices), ...catalogIds])).sort();

  // RM-62: modelId -> its node's $/hour + margin, via the catalog's node-name
  // link (registry.py's ModelEntry.node) -> the node registry (Nodes page).
  const nodeByName = new Map((nodesQuery.data ?? []).map((n) => [n.name, n]));
  const nodeInfoByModel = new Map(
    catalogEntries.map((m) => {
      const node = nodeByName.get(m.node);
      return [
        m.id,
        node
          ? { hourlyCostUsd: node.hourly_cost_usd, margin: node.price_margin_multiplier }
          : null,
      ] as const;
    }),
  );
  const backendsById = metricsQuery.data?.backends ?? {};

  if (modelIds.length === 0) {
    return (
      <div className="p-8 text-center text-text-muted">
        No models registered yet — add one from Models first.
      </div>
    );
  }

  return (
    <div>
      <div className="border-b border-border bg-surface px-4 py-3 text-xs text-text-muted">
        The calculator button suggests a price from each model's node cost + margin — set both
        on the Nodes page.
      </div>
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="sticky top-0 z-10 bg-surface">
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">Model</th>
            <th className="px-4 py-3 font-medium">Prompt $/1M</th>
            <th className="px-4 py-3 font-medium">Completion $/1M</th>
            <th className="px-4 py-3 font-medium">Image $/each</th>
            <th className="px-4 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {modelIds.map((modelId) => {
            const nodeInfo = nodeInfoByModel.get(modelId);
            return (
              <ModelPricingRow
                key={modelId}
                modelId={modelId}
                entry={prices[modelId]}
                hourlyCostUsd={nodeInfo?.hourlyCostUsd ?? null}
                metrics={backendsById[modelId]}
                margin={nodeInfo?.margin ?? 1}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
