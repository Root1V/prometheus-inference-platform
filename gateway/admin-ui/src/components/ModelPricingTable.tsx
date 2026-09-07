import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";
import { useDeleteModelPrice, useModelPrices, useUpdateModelPrice } from "../api/billing";
import { useModelCatalog } from "../api/models";
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

function ModelPricingRow({ modelId, entry }: { modelId: string; entry: ModelPriceEntry | undefined }) {
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
            className={
              "ml-2 rounded-full px-2 py-0.5 text-xs " +
              (isDbOverride
                ? "bg-primary/10 text-primary"
                : "bg-background text-text-muted")
            }
          >
            {isDbOverride ? "custom" : "pricing.yaml"}
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

  if (pricesQuery.isLoading || catalogQuery.isLoading) {
    return <div className="p-8 text-center text-text-muted">Loading…</div>;
  }

  const prices = pricesQuery.data?.data ?? {};
  const catalogIds = (catalogQuery.data?.models ?? []).map((m) => m.id);
  // Every priced model (yaml or db) plus every catalog model not yet priced —
  // a model can be priced before it's downloaded, but usually it's the
  // other way around.
  const modelIds = Array.from(new Set([...Object.keys(prices), ...catalogIds])).sort();

  if (modelIds.length === 0) {
    return (
      <div className="p-8 text-center text-text-muted">
        No models registered yet — add one from Models first.
      </div>
    );
  }

  return (
    <table className="w-full min-w-[640px] text-left text-sm">
      <thead>
        <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
          <th className="px-4 py-3 font-medium">Model</th>
          <th className="px-4 py-3 font-medium">Prompt $/1M</th>
          <th className="px-4 py-3 font-medium">Completion $/1M</th>
          <th className="px-4 py-3 font-medium">Image $/each</th>
          <th className="px-4 py-3 font-medium">Actions</th>
        </tr>
      </thead>
      <tbody>
        {modelIds.map((modelId) => (
          <ModelPricingRow key={modelId} modelId={modelId} entry={prices[modelId]} />
        ))}
      </tbody>
    </table>
  );
}
