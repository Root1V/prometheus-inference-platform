import { Ban, Download, RotateCcw, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import {
  useCancelDownload,
  useDeleteDownloadedModel,
  useDownloads,
  useModelCard,
  useModelFiles,
  useModelSearch,
  useRetryDownload,
  useStartDownload,
} from "../api/models";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import { Sidebar } from "../components/Sidebar";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { DownloadEntry, ModelSort } from "../types/models";

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

const SORT_OPTIONS: { value: ModelSort | ""; label: string }[] = [
  { value: "", label: "Relevance" },
  { value: "downloads", label: "Most downloads" },
  { value: "likes", label: "Most likes" },
  { value: "trending_score", label: "Trending" },
  { value: "last_modified", label: "Recently updated" },
  { value: "created_at", label: "Newest" },
];

function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(1)} ${units[i]}`;
}

function formatCount(n: number | null): string {
  if (n === null) return "?";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

const STATUS_COLOR: Record<DownloadEntry["status"], string> = {
  queued: "text-text-muted",
  downloading: "text-primary",
  verifying: "text-primary",
  done: "text-green-600",
  failed: "text-red-600",
  cancelled: "text-text-muted",
};

const _ACTIVE = new Set(["queued", "downloading", "verifying"]);

function DownloadRow({ entry, node }: { entry: DownloadEntry; node: string }) {
  const { showToast } = useToast();
  const cancelDownload = useCancelDownload();
  const retryDownload = useRetryDownload();
  const isActive = _ACTIVE.has(entry.status);

  return (
    <div className="rounded-lg border border-border bg-background p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-text">{entry.model_id}</span>
        <span className={cn("text-xs font-medium", STATUS_COLOR[entry.status])}>{entry.status}</span>
      </div>
      <p className="mt-0.5 truncate text-xs text-text-muted">{entry.hf_repo}</p>
      {isActive && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-border">
          <div
            className="h-full bg-primary transition-all"
            style={{ width: `${Math.round(entry.progress * 100)}%` }}
          />
        </div>
      )}
      <div className="mt-2 flex items-center justify-between text-xs text-text-muted">
        <span>
          {formatBytes(entry.downloaded_bytes)}
          {entry.total_bytes > 0 ? ` / ${formatBytes(entry.total_bytes)}` : ""}
        </span>
        <div className="flex items-center gap-2">
          {isActive && (
            <button
              type="button"
              onClick={() =>
                cancelDownload.mutate(
                  { node, modelId: entry.model_id.split(" [")[0] },
                  { onError: (e) => showToast(getErrorMessage(e), "error") },
                )
              }
              title="Cancel"
              className="flex items-center gap-1 text-text-muted hover:text-red-600"
            >
              <Ban size={14} />
            </button>
          )}
          {entry.status === "failed" && (
            <button
              type="button"
              onClick={() =>
                retryDownload.mutate(
                  { node, modelId: entry.model_id.split(" [")[0] },
                  { onError: (e) => showToast(getErrorMessage(e), "error") },
                )
              }
              title="Retry"
              className="flex items-center gap-1 text-text-muted hover:text-text"
            >
              <RotateCcw size={14} />
            </button>
          )}
        </div>
      </div>
      {entry.error && <p className="mt-1 text-xs text-red-600">{entry.error}</p>}
    </div>
  );
}

export default function Models() {
  const { showToast } = useToast();
  const nodesQuery = useNodeRegistry();
  // Only active nodes are actually reachable — fetch_nodes() on the manager-api
  // side filters inactive ones out, so offering them here would just 400.
  const nodes = (nodesQuery.data ?? []).filter((n) => n.is_active).map((n) => n.name);
  const [node, setNode] = useState("");
  const selectedNode = node || nodes[0] || "";

  const [query, setQuery] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [sort, setSort] = useState<ModelSort | "">("");
  const [selectedRepo, setSelectedRepo] = useState("");
  const [showCard, setShowCard] = useState(false);

  const searchQuery = useModelSearch(selectedNode, searchTerm, sort);
  const filesQuery = useModelFiles(selectedNode, selectedRepo);
  const cardQuery = useModelCard(selectedNode, showCard ? selectedRepo : "");
  const downloadsQuery = useDownloads(selectedNode);
  const instancesQuery = useInstances();
  const startDownload = useStartDownload();
  const deleteDownloaded = useDeleteDownloadedModel();

  const downloadedModels = (instancesQuery.data?.instances ?? []).filter((i) => i.downloaded);

  function handleSearch() {
    setSearchTerm(query.trim());
    setSelectedRepo("");
    setShowCard(false);
  }

  function handleDownload(filename: string) {
    if (!selectedNode) return;
    startDownload.mutate(
      { node: selectedNode, data: { repo_id: selectedRepo, filename } },
      {
        onSuccess: (result) => showToast(`Downloading ${result.model_id}…`, "success"),
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  function handleDelete(modelId: string) {
    if (!selectedNode) return;
    deleteDownloaded.mutate(
      { node: selectedNode, modelId },
      {
        onSuccess: () => showToast(`${modelId} deleted`, "success"),
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-text">Models</h1>
            <p className="mt-1 text-sm text-text-muted">
              Search Hugging Face, download a GGUF model, and manage what's on disk — only
              downloaded models can be selected when creating an instance.
            </p>
          </div>
          {nodes.length > 0 && (
            <select
              value={selectedNode}
              onChange={(e) => setNode(e.target.value)}
              className={cn(inputClass, "w-48")}
            >
              {nodes.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          )}
        </div>

        {nodes.length === 0 ? (
          <div className="mt-6 rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
            {(nodesQuery.data?.length ?? 0) === 0
              ? "No nodes configured — add one from the Nodes page first."
              : "No active nodes — check connectivity from the Nodes page."}
          </div>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-4">
              <div className="rounded-xl border border-border bg-surface p-4">
                <h2 className="mb-3 text-sm font-semibold text-text">Search Hugging Face</h2>
                <div className="flex gap-2">
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                    placeholder="e.g. llama-3.2, nomic-embed-text…"
                    className={cn(inputClass, "flex-1")}
                  />
                  <button
                    type="button"
                    onClick={handleSearch}
                    disabled={!query.trim()}
                    className="flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Search size={16} />
                  </button>
                </div>

                {searchTerm && (
                  <div className="mt-2 flex items-center gap-2">
                    <label htmlFor="model-sort" className="text-xs text-text-muted">
                      Sort by
                    </label>
                    <select
                      id="model-sort"
                      value={sort}
                      onChange={(e) => setSort(e.target.value as ModelSort | "")}
                      className={cn(inputClass, "w-auto py-1 text-xs")}
                    >
                      {SORT_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
                  {searchQuery.isLoading && (
                    <p className="text-sm text-text-muted">Searching…</p>
                  )}
                  {searchQuery.isError && (
                    <p className="text-sm text-red-600">{getErrorMessage(searchQuery.error)}</p>
                  )}
                  {searchQuery.data?.length === 0 && (
                    <p className="text-sm text-text-muted">No results.</p>
                  )}
                  {searchQuery.data?.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      onClick={() => {
                        setSelectedRepo(r.id);
                        setShowCard(false);
                      }}
                      className={cn(
                        "block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-background",
                        selectedRepo === r.id && "bg-background ring-1 ring-primary",
                      )}
                    >
                      <span className="font-medium text-text">{r.id}</span>
                      <span className="ml-2 text-xs text-text-muted">
                        ↓{formatCount(r.downloads)} · ★{formatCount(r.likes)}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {selectedRepo && (
                <div className="rounded-xl border border-border bg-surface p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-text">{selectedRepo}</h2>
                    <button
                      type="button"
                      onClick={() => setShowCard((v) => !v)}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {showCard ? "Hide model card" : "Show model card"}
                    </button>
                  </div>

                  {showCard && (
                    <div className="mb-3 max-h-56 overflow-y-auto rounded-lg border border-border bg-background p-3">
                      {cardQuery.isLoading && (
                        <p className="text-sm text-text-muted">Loading model card…</p>
                      )}
                      {cardQuery.isError && (
                        <p className="text-sm text-red-600">{getErrorMessage(cardQuery.error)}</p>
                      )}
                      {cardQuery.data && (
                        <pre className="whitespace-pre-wrap font-sans text-xs text-text">
                          {cardQuery.data.text}
                        </pre>
                      )}
                    </div>
                  )}

                  <div className="max-h-64 space-y-1 overflow-y-auto">
                    {filesQuery.isLoading && (
                      <p className="text-sm text-text-muted">Loading files…</p>
                    )}
                    {filesQuery.data?.map((f) => (
                      <div
                        key={f.filename}
                        className="flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-background"
                      >
                        <div>
                          <span className="font-mono text-xs text-text">{f.filename}</span>
                          <span className="ml-2 text-xs text-text-muted">
                            {f.quantization} · {f.size_bytes !== null ? formatBytes(f.size_bytes) : "? size"}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDownload(f.filename)}
                          disabled={startDownload.isPending}
                          title="Download"
                          className="text-text-muted hover:text-primary disabled:opacity-40"
                        >
                          <Download size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-4">
              <div className="rounded-xl border border-border bg-surface p-4">
                <h2 className="mb-3 text-sm font-semibold text-text">Downloads</h2>
                {(downloadsQuery.data?.length ?? 0) === 0 ? (
                  <p className="text-sm text-text-muted">No downloads yet.</p>
                ) : (
                  <div className="space-y-2">
                    {downloadsQuery.data?.map((d) => (
                      <DownloadRow key={d.model_id} entry={d} node={selectedNode} />
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-border bg-surface p-4">
                <h2 className="mb-3 text-sm font-semibold text-text">Downloaded models</h2>
                {downloadedModels.length === 0 ? (
                  <p className="text-sm text-text-muted">
                    Nothing downloaded yet — search above to get started.
                  </p>
                ) : (
                  <div className="space-y-1">
                    {downloadedModels.map((m) => (
                      <div
                        key={m.id}
                        className="flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-background"
                      >
                        <div>
                          <span className="font-medium text-text">{m.id}</span>
                          <span className="ml-2 text-xs text-text-muted">
                            {m.family} · {m.quantization}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDelete(m.id)}
                          disabled={deleteDownloaded.isPending}
                          title="Delete downloaded file"
                          className="text-text-muted hover:text-red-600 disabled:opacity-40"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
