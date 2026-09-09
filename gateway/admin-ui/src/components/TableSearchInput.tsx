import { Search, X } from "lucide-react";

/**
 * RM-59: shared search box for the Instances and Models tables. Both filter
 * client-side over data they already hold — no new endpoint, no refetch — so
 * this is purely a controlled input; the owning table decides which fields a
 * query matches against.
 */
export function TableSearchInput({
  value,
  onChange,
  placeholder,
  resultLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** e.g. "3 of 27" — only rendered while a query is active. */
  resultLabel?: string;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-3">
      <div className="relative flex-1">
        <Search
          size={14}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
        />
        <input
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          className="w-full rounded-lg border border-border bg-background py-1.5 pl-8 pr-8 text-sm text-text focus:border-primary focus:outline-none"
        />
        {value !== "" && (
          <button
            type="button"
            onClick={() => onChange("")}
            aria-label="Clear search"
            title="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-muted hover:text-text"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {value !== "" && resultLabel && (
        <span className="shrink-0 text-xs text-text-muted">{resultLabel}</span>
      )}
    </div>
  );
}
