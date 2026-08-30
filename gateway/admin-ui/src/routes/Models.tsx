import { Ban, Download, Pause, Play, RotateCcw, Search, Settings } from "lucide-react";
import { useState } from "react";
import {
  useCancelDownload,
  useDownloads,
  useModelFiles,
  useModelSearch,
  usePauseDownload,
  useResumeDownload,
  useRetryDownload,
  useStartDownload,
} from "../api/models";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import { DownloadedModelsTable } from "../components/DownloadedModelsTable";
import { ModelCardView } from "../components/ModelCardView";
import { ModelPreviewPanel } from "../components/ModelPreviewPanel";
import { ModelSettingsModal } from "../components/ModelSettingsModal";
import { Sidebar } from "../components/Sidebar";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { formatBytes } from "../lib/format";
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
  paused: "text-amber-600",
};

const _ACTIVE = new Set(["queued", "downloading", "verifying"]);

function DownloadRow({ entry, node }: { entry: DownloadEntry; node: string }) {
  const { showToast } = useToast();
  const cancelDownload = useCancelDownload();
  const pauseDownload = usePauseDownload();
  const resumeDownload = useResumeDownload();
  const retryDownload = useRetryDownload();
  const isActive = _ACTIVE.has(entry.status);
  const isPaused = entry.status === "paused";
  const baseModelId = entry.model_id.split(" [")[0];

  return (
    <div className="rounded-lg border border-border bg-background p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-text">{entry.model_id}</span>
        <span className={cn("text-xs font-medium", STATUS_COLOR[entry.status])}>{entry.status}</span>
      </div>
      <p className="mt-0.5 truncate text-xs text-text-muted">{entry.hf_repo}</p>
      {(isActive || isPaused) && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-border">
          <div
            className={cn("h-full transition-all", isPaused ? "bg-amber-500" : "bg-primary")}
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
                pauseDownload.mutate(
                  { node, modelId: baseModelId },
                  { onError: (e) => showToast(getErrorMessage(e), "error") },
                )
              }
              title="Pause"
              className="flex items-center gap-1 text-text-muted hover:text-amber-600"
            >
              <Pause size={14} />
            </button>
          )}
          {isPaused && (
            <button
              type="button"
              onClick={() =>
                resumeDownload.mutate(
                  { node, modelId: baseModelId },
                  { onError: (e) => showToast(getErrorMessage(e), "error") },
                )
              }
              title="Resume"
              className="flex items-center gap-1 text-text-muted hover:text-primary"
            >
              <Play size={14} />
            </button>
          )}
          {isActive && (
            <button
              type="button"
              onClick={() =>
                cancelDownload.mutate(
                  { node, modelId: baseModelId },
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
                  { node, modelId: baseModelId },
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

type Tab = "discover" | "library";

export default function Models() {
  const { showToast } = useToast();
  const nodesQuery = useNodeRegistry();
  // Only active nodes are actually reachable — fetch_nodes() on the manager-api
  // side filters inactive ones out, so offering them here would just 400.
  const nodes = (nodesQuery.data ?? []).filter((n) => n.is_active).map((n) => n.name);
  const [node, setNode] = useState("");
  const selectedNode = node || nodes[0] || "";

  const [tab, setTab] = useState<Tab>("discover");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [query, setQuery] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [sort, setSort] = useState<ModelSort | "">("");
  const [selectedRepo, setSelectedRepo] = useState("");
  const [showCard, setShowCard] = useState(false);

  const [previewId, setPreviewId] = useState<string | null>(null);

  const searchQuery = useModelSearch(selectedNode, searchTerm, sort);
  const filesQuery = useModelFiles(selectedNode, selectedRepo);
  const downloadsQuery = useDownloads(selectedNode);
  const instancesQuery = useInstances();
  const startDownload = useStartDownload();

  const downloadedModels = (instancesQuery.data?.instances ?? []).filter((i) => i.downloaded);
  const previewModel = previewId ? (downloadedModels.find((m) => m.id === previewId) ?? null) : null;

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

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-text">Models</h1>
            <p className="mt-1 text-sm text-text-muted">
              Search Hugging Face, download a GGUF model, and manage what's on disk — only
              downloaded models can be selected when creating an instance.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {nodes.length > 0 && (
              <select
                value={selectedNode}
                onChange={(e) => {
                  setNode(e.target.value);
                  setPreviewId(null);
                }}
                className={cn(inputClass, "w-48")}
              >
                {nodes.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              disabled={!selectedNode}
              title="Model settings"
              aria-label="Model settings"
              className="rounded-lg border border-border p-2 text-text-muted hover:bg-surface hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Settings size={18} />
            </button>
          </div>
        </div>

        {nodes.length === 0 ? (
          <div className="mt-6 rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
            {(nodesQuery.data?.length ?? 0) === 0
              ? "No nodes configured — add one from the Nodes page first."
              : "No active nodes — check connectivity from the Nodes page."}
          </div>
        ) : (
          <>
            <div className="mt-4 flex gap-2 border-b border-border">
              <button
                type="button"
                onClick={() => setTab("discover")}
                className={cn(
                  "border-b-2 px-3 py-2 text-sm font-medium",
                  tab === "discover"
                    ? "border-primary text-text"
                    : "border-transparent text-text-muted hover:text-text",
                )}
              >
                Discover
              </button>
              <button
                type="button"
                onClick={() => setTab("library")}
                className={cn(
                  "border-b-2 px-3 py-2 text-sm font-medium",
                  tab === "library"
                    ? "border-primary text-text"
                    : "border-transparent text-text-muted hover:text-text",
                )}
              >
                Library
                {downloadedModels.length > 0 && (
                  <span className="ml-1.5 text-xs text-text-muted">({downloadedModels.length})</span>
                )}
              </button>
            </div>

            {tab === "discover" ? (
              <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
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

                  <div className="mt-3 max-h-[28rem] space-y-1 overflow-y-auto">
                    {searchQuery.isLoading && <p className="text-sm text-text-muted">Searching…</p>}
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

                <div className="rounded-xl border border-border bg-surface p-4">
                  {selectedRepo ? (
                    <>
                      <div className="mb-3 flex items-center justify-between">
                        <h2 className="truncate text-sm font-semibold text-text">{selectedRepo}</h2>
                        <button
                          type="button"
                          onClick={() => setShowCard((v) => !v)}
                          className="shrink-0 text-xs font-medium text-primary hover:underline"
                        >
                          {showCard ? "Hide model card" : "Show model card"}
                        </button>
                      </div>

                      {showCard && (
                        <div className="mb-3 max-h-56 overflow-y-auto rounded-lg border border-border bg-background p-3">
                          <ModelCardView node={selectedNode} repoId={selectedRepo} />
                        </div>
                      )}

                      <div className="max-h-[24rem] space-y-1 overflow-y-auto">
                        {filesQuery.isLoading && (
                          <p className="text-sm text-text-muted">Loading files…</p>
                        )}
                        {filesQuery.data?.map((f) => (
                          <div
                            key={f.filename}
                            className="flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-background"
                          >
                            <div className="min-w-0">
                              <span className="block truncate font-mono text-xs text-text">
                                {f.filename}
                              </span>
                              <span className="text-xs text-text-muted">
                                {f.quantization} ·{" "}
                                {f.size_bytes !== null ? formatBytes(f.size_bytes) : "? size"}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleDownload(f.filename)}
                              disabled={startDownload.isPending}
                              title="Download"
                              className="shrink-0 text-text-muted hover:text-primary disabled:opacity-40"
                            >
                              <Download size={16} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="flex h-full min-h-[16rem] items-center justify-center text-center text-sm text-text-muted">
                      Select a result to see its files.
                    </div>
                  )}
                </div>

                <div className="rounded-xl border border-border bg-surface p-4">
                  <h2 className="mb-3 text-sm font-semibold text-text">Downloads</h2>
                  {(downloadsQuery.data?.length ?? 0) === 0 ? (
                    <p className="text-sm text-text-muted">No downloads yet.</p>
                  ) : (
                    <div className="max-h-[32rem] space-y-2 overflow-y-auto">
                      {downloadsQuery.data?.map((d) => (
                        <DownloadRow key={d.model_id} entry={d} node={selectedNode} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="mt-6 flex items-start gap-6">
                <div className="min-w-0 flex-1">
                  <DownloadedModelsTable
                    models={downloadedModels}
                    node={selectedNode}
                    selectedId={previewId}
                    onSelect={(m) => setPreviewId(m.id === previewId ? null : m.id)}
                  />
                </div>
                {previewModel && (
                  <ModelPreviewPanel
                    model={previewModel}
                    node={selectedNode}
                    onClose={() => setPreviewId(null)}
                  />
                )}
              </div>
            )}
          </>
        )}
      </main>

      <ModelSettingsModal open={settingsOpen} node={selectedNode} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
