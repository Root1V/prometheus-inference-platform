import {
  Ban,
  Download,
  HardDrive,
  Layers,
  Pause,
  Play,
  RotateCcw,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import {
  useCancelDownload,
  useDownloads,
  useModelCatalog,
  useModelFiles,
  useModelsConfig,
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
import { StatCard } from "../components/StatCard";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { formatBytes } from "../lib/format";
import type { DownloadEntry, HfFile, ModelSort } from "../types/models";

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

/**
 * Mirrors `_SHARD_RE` in `hf_discovery.py` — PRM-210.
 *
 * It has to, because the node acts on the group rather than the file: picking
 * one shard calls `shard_filenames()`, which collects every sibling and
 * downloads all of them. Listing each shard separately with its own size
 * therefore understated what the button did, measured on a real repo, by two
 * and a half times — a row reading 26.8 GB fetched 68.0 GB.
 */
const SHARD_RE = /^(.*?-?)(\d{5})-of-(\d{5})(\.gguf)$/i;

interface FileGroup {
  /** The filename to hand the node; it expands the rest itself. */
  filename: string;
  label: string;
  quantization: string;
  /** Summed across the set. Null if any member did not report a size — a
   * partial sum presented as the whole is the same lie as a missing one. */
  totalBytes: number | null;
  parts: number;
}

function groupShards(files: HfFile[]): FileGroup[] {
  const groups = new Map<string, HfFile[]>();
  for (const f of files) {
    const m = SHARD_RE.exec(f.filename);
    groups.set(m ? `${m[1]}|of|${m[3]}${m[4]}` : f.filename, [
      ...(groups.get(m ? `${m[1]}|of|${m[3]}${m[4]}` : f.filename) ?? []),
      f,
    ]);
  }
  return [...groups.values()].map((members) => {
    const sorted = [...members].sort((a, b) =>
      a.filename.localeCompare(b.filename),
    );
    const first = sorted[0];
    const anyUnsized = sorted.some((f) => f.size_bytes === null);
    return {
      filename: first.filename,
      label:
        sorted.length > 1
          ? first.filename.replace(SHARD_RE, "$1*$4")
          : first.filename,
      quantization: first.quantization,
      totalBytes: anyUnsized
        ? null
        : sorted.reduce((n, f) => n + (f.size_bytes ?? 0), 0),
      parts: sorted.length,
    };
  });
}

/**
 * PRM-211: a washed-out orange button reads as broken, not as "not yet".
 *
 * `disabled:opacity-40` over a saturated primary produced a pale smear that
 * looked like a rendering fault on the one control the tab is for. Disabled
 * now means a neutral, obviously-inert button, and the label stays visible
 * because an icon alone never said what pressing it would do.
 */
function SearchButton({
  onClick,
  disabled,
  compact,
}: {
  onClick: () => void;
  disabled: boolean;
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Search Hugging Face"
      className={cn(
        "flex shrink-0 items-center gap-2 rounded-lg px-4 text-sm font-medium transition-colors",
        compact ? "py-2" : "py-2.5",
        disabled
          ? "cursor-not-allowed bg-background text-text-muted"
          : "bg-primary text-primary-foreground hover:opacity-90",
      )}
    >
      <Search size={16} />
      {!compact && "Search"}
    </button>
  );
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
        <span className={cn("text-xs font-medium", STATUS_COLOR[entry.status])}>
          {entry.status}
        </span>
      </div>
      <p className="mt-0.5 truncate text-xs text-text-muted">{entry.hf_repo}</p>
      {(isActive || isPaused) && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-border">
          <div
            className={cn(
              "h-full transition-all",
              isPaused ? "bg-amber-500" : "bg-primary",
            )}
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
      {entry.error && (
        <p className="mt-1 text-xs text-red-600">{entry.error}</p>
      )}
    </div>
  );
}

type Tab = "discover" | "library";

export default function Models() {
  const { showToast } = useToast();
  const nodesQuery = useNodeRegistry();
  // Only active nodes are actually reachable — fetch_nodes() on the manager-api
  // side filters inactive ones out, so offering them here would just 400.
  const nodes = (nodesQuery.data ?? [])
    .filter((n) => n.is_active)
    .map((n) => n.name);
  const [node, setNode] = useState("");
  const selectedNode = node || nodes[0] || "";

  const [tab, setTab] = useState<Tab>("library");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [query, setQuery] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [sort, setSort] = useState<ModelSort | "">("");
  const [selectedRepo, setSelectedRepo] = useState("");
  const [showCard, setShowCard] = useState(false);
  const [customModelId, setCustomModelId] = useState("");

  const [previewId, setPreviewId] = useState<string | null>(null);

  const searchQuery = useModelSearch(selectedNode, searchTerm, sort);
  const filesQuery = useModelFiles(selectedNode, selectedRepo);
  const downloadsQuery = useDownloads(selectedNode);
  const configQuery = useModelsConfig(selectedNode);
  const instancesQuery = useInstances();
  const catalogQuery = useModelCatalog();
  const startDownload = useStartDownload();

  const instances = instancesQuery.data?.instances ?? [];
  const downloadedModels = (catalogQuery.data?.models ?? []).filter(
    (m) => m.node === selectedNode,
  );
  /** Repo ids already downloaded on this node, so a search result can say so
   *  rather than letting someone re-fetch 68 GB they already have. */
  const ownedRepos = new Set(
    downloadedModels
      .filter((m) => m.hf_repo)
      .map((m) => m.hf_repo.toLowerCase()),
  );
  const fileGroups = groupShards(filesQuery.data ?? []);
  const freeBytes = configQuery.data?.disk_free_bytes ?? null;

  const previewModel = previewId
    ? (downloadedModels.find((m) => m.id === previewId) ?? null)
    : null;

  /**
   * PRM-209: this page's own description says it manages what is on disk, and
   * it never said how much that was. Measured on this platform the answer is
   * 523.2 GB across 32 local models, of which 443.5 GB — 85% — is held by 21
   * models with no instance. Reconstructing that meant reading every row and
   * adding by hand.
   *
   * `downloaded` is the discriminator throughout: a model served from another
   * host occupies nothing here and must not be counted as if it did. And the
   * total says how many models it could not measure rather than quietly
   * summing the rest, which is the same rule RM-33 applies to unpriced usage.
   */
  const disk = (() => {
    const onDisk = downloadedModels.filter((m) => m.downloaded);
    const unsized = onDisk.filter((m) => m.file_size_bytes === null).length;
    // Counted across EVERY model, not just the downloaded ones, so this agrees
    // with the table's own "No instance" filter. A registered model nothing
    // runs is dead weight in the registry whether or not it holds a disk.
    const idle = downloadedModels.filter((m) => m.instance_ids.length === 0);
    const sum = (ms: typeof onDisk) =>
      ms.reduce((n, m) => n + (m.file_size_bytes ?? 0), 0);
    return {
      onDisk: onDisk.length,
      unsized,
      totalBytes: sum(onDisk),
      idleCount: idle.length,
      idleBytes: sum(idle),
      remote: downloadedModels.length - onDisk.length,
    };
  })();

  function handleSearch() {
    setSearchTerm(query.trim());
    setSelectedRepo("");
    setShowCard(false);
    setCustomModelId("");
  }

  function handleDownload(filename: string) {
    if (!selectedNode) return;
    const modelId = customModelId.trim();
    startDownload.mutate(
      {
        node: selectedNode,
        data: {
          repo_id: selectedRepo,
          filename,
          ...(modelId ? { model_id: modelId } : {}),
        },
      },
      {
        onSuccess: (result) =>
          showToast(`Downloading ${result.model_id}…`, "success"),
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
              {/* PRM-209: it used to end "only downloaded models can be
                  selected when creating an instance", which this page's own
                  table contradicts — `minilm-hfserve` is not downloaded and
                  has two. */}
              Search Hugging Face, download GGUF weights, and manage what's on
              disk. Models served from another host appear here too, holding no
              space on this node.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* PRM-209: labelled. It governs both tabs, so the header is the
                right place for it — but a bare select reading "lab" never said
                what it selected, and switching it turns 8 rows into 32. */}
            {nodes.length > 0 && (
              <label className="flex items-center gap-2 text-sm text-text-muted">
                Node
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
              </label>
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
                  <span className="ml-1.5 text-xs text-text-muted">
                    ({downloadedModels.length})
                  </span>
                )}
              </button>
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
            </div>

            {tab === "discover" ? (
              <>
                {/* What a download will cost, against what there is room for.
                    The catalog reports what the weights occupy and never what
                    is left; the node now answers that too (PRM-210). */}
                <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3 text-sm">
                  <span className="text-text-muted">
                    Downloads land on{" "}
                    <span className="font-medium text-text">
                      {selectedNode}
                    </span>
                  </span>
                  <span className="text-text-muted">
                    {freeBytes === null ? (
                      "Free space unknown on this node"
                    ) : (
                      <>
                        <span className="font-medium text-text">
                          {formatBytes(freeBytes)}
                        </span>{" "}
                        free
                        {configQuery.data?.disk_total_bytes
                          ? ` of ${formatBytes(configQuery.data.disk_total_bytes)}`
                          : ""}
                      </>
                    )}
                  </span>
                  {disk.totalBytes > 0 && (
                    <span className="text-text-muted">
                      {formatBytes(disk.totalBytes)} already in models here
                    </span>
                  )}
                </div>

                {/* PRM-211: directly under the bar, not at the foot of the page.
                    This is progress on the thing you just clicked, and it used
                    to appear below everything else — off-screen at the moment
                    it mattered most. Full-width rows rather than a three-column
                    grid, which left one download sitting in a third of a card
                    with two thirds of nothing beside it. */}
                {(downloadsQuery.data?.length ?? 0) > 0 && (
                  <div className="mt-4 rounded-xl border border-border bg-surface p-4">
                    <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-text-muted">
                      Downloads
                    </h2>
                    {/* In-flight first, and the whole block bounded. Moving
                        this to the top gave finished entries the best seat on
                        the page, and the manager keeps them for the session —
                        so without an order and a ceiling a morning of
                        downloads would push the search off-screen. */}
                    <div className="max-h-60 space-y-2 overflow-y-auto">
                      {[...(downloadsQuery.data ?? [])]
                        .sort(
                          (a, b) =>
                            Number(
                              _ACTIVE.has(b.status) || b.status === "paused",
                            ) -
                            Number(
                              _ACTIVE.has(a.status) || a.status === "paused",
                            ),
                        )
                        .map((d) => (
                          <DownloadRow
                            key={d.model_id}
                            entry={d}
                            node={selectedNode}
                          />
                        ))}
                    </div>
                  </div>
                )}

                {!searchTerm ? (
                  /* PRM-211: before a search there is exactly one thing to do
                     here, and it was a small box in the corner of an otherwise
                     empty screen with a tall blank panel beside it. One
                     affordance, centred, sized like the only action it is. */
                  <div className="mt-4 rounded-xl border border-border bg-surface px-6 py-14">
                    <div className="mx-auto max-w-xl text-center">
                      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                        <Search size={20} />
                      </div>
                      <h2 className="mt-4 text-base font-semibold text-text">
                        Search Hugging Face
                      </h2>
                      <p className="mt-1 text-sm text-text-muted">
                        Find GGUF weights by name, or paste a repo id such as{" "}
                        <span className="font-mono text-xs">
                          unsloth/Qwen3-0.6B-GGUF
                        </span>
                        .
                      </p>
                      <div className="mt-5 flex gap-2">
                        <input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                          placeholder="e.g. llama-3.2, nomic-embed-text…"
                          className={cn(inputClass, "flex-1 py-2.5")}
                          autoFocus
                        />
                        <SearchButton
                          onClick={handleSearch}
                          disabled={!query.trim()}
                        />
                      </div>
                    </div>
                  </div>
                ) : (
                  /* `items-start`: without it the grid stretches both columns to
                     the taller one, so three file rows sat at the top of a panel
                     seven hundred pixels deep. */
                  <div className="mt-4 grid grid-cols-1 items-start gap-6 lg:grid-cols-5">
                    <div className="rounded-xl border border-border bg-surface p-4 lg:col-span-2">
                      <div className="flex gap-2">
                        <input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                          placeholder="e.g. llama-3.2, nomic-embed-text…"
                          className={cn(inputClass, "flex-1")}
                        />
                        <SearchButton
                          onClick={handleSearch}
                          disabled={!query.trim()}
                          compact
                        />
                      </div>

                      <div className="mt-3 flex items-center gap-2">
                        <label
                          htmlFor="model-sort"
                          className="text-xs text-text-muted"
                        >
                          Sort by
                        </label>
                        <select
                          id="model-sort"
                          value={sort}
                          onChange={(e) =>
                            setSort(e.target.value as ModelSort | "")
                          }
                          className={cn(inputClass, "w-auto py-1 text-xs")}
                        >
                          {SORT_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                        {searchQuery.data && (
                          <span className="ml-auto text-xs text-text-muted">
                            {searchQuery.data.length} result
                            {searchQuery.data.length === 1 ? "" : "s"}
                          </span>
                        )}
                      </div>

                      <div className="mt-3 max-h-[30rem] space-y-2 overflow-y-auto">
                        {searchQuery.isLoading && (
                          <p className="px-1 text-sm text-text-muted">
                            Searching…
                          </p>
                        )}
                        {searchQuery.isError && (
                          <p className="px-1 text-sm text-red-600">
                            {getErrorMessage(searchQuery.error)}
                          </p>
                        )}
                        {searchQuery.data?.length === 0 && (
                          <p className="px-1 text-sm text-text-muted">
                            Nothing matched “{searchTerm}”.
                          </p>
                        )}
                        {/* A bordered card per result. They were borderless
                            text in a column, so nothing said a row was a thing
                            you could pick. */}
                        {searchQuery.data?.map((r) => (
                          <button
                            key={r.id}
                            type="button"
                            onClick={() => {
                              setSelectedRepo(r.id);
                              setShowCard(false);
                              setCustomModelId("");
                            }}
                            className={cn(
                              "block w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                              selectedRepo === r.id
                                ? "border-primary bg-primary/5"
                                : "border-border hover:border-primary/40 hover:bg-background",
                            )}
                          >
                            <span className="block break-words text-sm font-medium text-text">
                              {r.id}
                            </span>
                            <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
                              <span className="rounded bg-background px-1.5 py-0.5">
                                ↓{formatCount(r.downloads)}
                              </span>
                              <span className="rounded bg-background px-1.5 py-0.5">
                                ★{formatCount(r.likes)}
                              </span>
                              {ownedRepos.has(r.id.toLowerCase()) && (
                                <span className="rounded bg-green-500/10 px-1.5 py-0.5 font-medium text-green-700 dark:text-green-400">
                                  in library
                                </span>
                              )}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="rounded-xl border border-border bg-surface p-4 lg:col-span-3">
                      {selectedRepo ? (
                        <>
                          <div className="mb-3 flex items-start justify-between gap-3">
                            <h2 className="break-words text-sm font-semibold text-text">
                              {selectedRepo}
                            </h2>
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
                              <ModelCardView
                                node={selectedNode}
                                repoId={selectedRepo}
                              />
                            </div>
                          )}

                          <label className="mb-4 block text-sm text-text">
                            <span className="mb-1 block text-xs font-medium text-text-muted">
                              Model name (optional)
                            </span>
                            <input
                              value={customModelId}
                              onChange={(e) => setCustomModelId(e.target.value)}
                              placeholder="Auto-generated if left blank"
                              className={inputClass}
                            />
                            <span className="mt-1 block text-xs text-text-muted">
                              Only settable now — once downloaded, the name
                              can't be changed.
                            </span>
                          </label>

                          {filesQuery.isLoading ? (
                            <p className="text-sm text-text-muted">
                              Loading files…
                            </p>
                          ) : fileGroups.length === 0 ? (
                            <p className="text-sm text-text-muted">
                              No GGUF files in this repo.
                            </p>
                          ) : (
                            <div className="divide-y divide-border overflow-hidden rounded-lg border border-border">
                              {fileGroups.map((g) => {
                                const fits =
                                  freeBytes === null || g.totalBytes === null
                                    ? null
                                    : g.totalBytes <= freeBytes;
                                return (
                                  <div
                                    key={g.filename}
                                    className="flex items-center justify-between gap-4 px-3 py-2.5 transition-colors hover:bg-background"
                                  >
                                    <div className="min-w-0">
                                      {/* The whole name, wrapped. Truncating it
                                          hid the one part that differs between
                                          two quantizations of the same repo. */}
                                      <span className="block break-all font-mono text-xs text-text">
                                        {g.label}
                                      </span>
                                      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                                        <span className="rounded bg-background px-1.5 py-0.5 text-text-muted">
                                          {g.quantization}
                                        </span>
                                        <span className="font-medium text-text">
                                          {g.totalBytes === null
                                            ? "size unknown"
                                            : formatBytes(g.totalBytes)}
                                        </span>
                                        {g.parts > 1 && (
                                          <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
                                            {g.parts} files, all fetched
                                          </span>
                                        )}
                                        {fits === false && (
                                          <span className="rounded bg-red-500/10 px-1.5 py-0.5 font-medium text-red-600 dark:text-red-400">
                                            larger than free space
                                          </span>
                                        )}
                                      </span>
                                    </div>
                                    {/* Labelled, bordered, and beside what it
                                        acts on. A bare icon at the far edge of
                                        a very wide panel put the control a
                                        thousand pixels from its subject. */}
                                    <button
                                      type="button"
                                      onClick={() => handleDownload(g.filename)}
                                      disabled={startDownload.isPending}
                                      title={
                                        g.parts > 1
                                          ? `Download all ${g.parts} parts to ${selectedNode}`
                                          : `Download to ${selectedNode}`
                                      }
                                      className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text transition-colors hover:border-primary hover:bg-primary hover:text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
                                    >
                                      <Download size={14} />
                                      Download
                                    </button>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="flex min-h-[10rem] items-center justify-center px-4 text-center text-sm text-text-muted">
                          Pick a result on the left to see its files and sizes.
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
                  {/* A dash is not an answer. On a node whose models are all
                      served from another host there is genuinely nothing on
                      disk, and saying so is information — rendering "—" for it
                      made the page look broken while showing eight real models
                      with nine real instances. */}
                  <StatCard
                    label="On disk"
                    value={
                      disk.totalBytes > 0
                        ? formatBytes(disk.totalBytes)
                        : disk.onDisk > 0
                          ? "Not reported"
                          : "None"
                    }
                    sub={
                      disk.onDisk === 0
                        ? `all ${downloadedModels.length} served from another host`
                        : disk.unsized > 0
                          ? `${disk.onDisk - disk.unsized} of ${disk.onDisk} model${disk.onDisk === 1 ? "" : "s"} measured`
                          : `across ${disk.onDisk} model${disk.onDisk === 1 ? "" : "s"}`
                    }
                    toneReason={
                      disk.unsized > 0
                        ? `${disk.unsized} downloaded model(s) did not report a size, so this total is a floor rather than the whole figure.`
                        : undefined
                    }
                    icon={HardDrive}
                  />
                  {/* Idle weights are the only number here anyone acts on. Warn
                    past half the disk: at that point the node is mostly
                    storing models nothing runs. */}
                  <StatCard
                    label="No instance"
                    value={
                      disk.idleBytes > 0
                        ? formatBytes(disk.idleBytes)
                        : disk.idleCount > 0
                          ? String(disk.idleCount)
                          : "None"
                    }
                    sub={
                      disk.idleCount === 0
                        ? "every model here is in use"
                        : disk.idleBytes > 0
                          ? `${disk.idleCount} model${disk.idleCount === 1 ? "" : "s"} nothing runs`
                          : `${disk.idleCount} model${disk.idleCount === 1 ? "" : "s"}, none on disk`
                    }
                    tone={
                      disk.totalBytes > 0 &&
                      disk.idleBytes / disk.totalBytes >= 0.5
                        ? "warn"
                        : "neutral"
                    }
                    toneReason={
                      disk.totalBytes > 0
                        ? `${Math.round((disk.idleBytes / disk.totalBytes) * 100)}% of this node's model storage is held by models with no instance.`
                        : undefined
                    }
                    icon={Trash2}
                  />
                  <StatCard
                    label="Models"
                    value={downloadedModels.length}
                    sub={
                      disk.remote > 0
                        ? `${disk.onDisk} on disk \u00b7 ${disk.remote} served elsewhere`
                        : `${disk.onDisk} on disk`
                    }
                    icon={Layers}
                  />
                </div>
                <div className="mt-4 flex items-start gap-6">
                  <div className="min-w-0 flex-1">
                    {/* Remount per node: the search text, the sort and the
                        "no instance" filter are all about *these* models, and
                        carrying them across emptied the table with no visible
                        cause. React's own answer to "reset state when a prop
                        changes" is a key, not an effect. */}
                    <DownloadedModelsTable
                      key={selectedNode}
                      models={downloadedModels}
                      instances={instances}
                      node={selectedNode}
                      selectedId={previewId}
                      onSelect={(m) =>
                        setPreviewId(m.id === previewId ? null : m.id)
                      }
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
              </>
            )}
          </>
        )}
      </main>

      <ModelSettingsModal
        open={settingsOpen}
        node={selectedNode}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
