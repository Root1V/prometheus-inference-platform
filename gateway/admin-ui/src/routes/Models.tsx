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

                <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-5">
                  <div className="lg:col-span-2">
                    <div className="rounded-xl border border-border bg-surface p-4">
                      <h2 className="mb-3 text-sm font-semibold text-text">
                        Search Hugging Face
                      </h2>
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
                        </div>
                      )}

                      <div className="mt-3 max-h-[28rem] space-y-1 overflow-y-auto">
                        {searchQuery.isLoading && (
                          <p className="text-sm text-text-muted">Searching…</p>
                        )}
                        {searchQuery.isError && (
                          <p className="text-sm text-red-600">
                            {getErrorMessage(searchQuery.error)}
                          </p>
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
                              setCustomModelId("");
                            }}
                            className={cn(
                              "block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-background",
                              selectedRepo === r.id &&
                                "bg-background ring-1 ring-primary",
                            )}
                          >
                            {/* `break-words`, not `truncate`: a repo id is the
                            only thing distinguishing two results and half of
                            one identifies nothing. */}
                            <span className="block break-words font-medium text-text">
                              {r.id}
                            </span>
                            <span className="mt-0.5 flex items-center gap-2 text-xs text-text-muted">
                              <span>↓{formatCount(r.downloads)}</span>
                              <span>★{formatCount(r.likes)}</span>
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
                  </div>

                  <div className="rounded-xl border border-border bg-surface p-4 lg:col-span-3">
                    {selectedRepo ? (
                      <>
                        <div className="mb-3 flex items-center justify-between">
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

                        <label className="mb-3 block text-sm text-text">
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
                            Only settable now — once downloaded, the name can't
                            be changed.
                          </span>
                        </label>

                        <div className="max-h-[24rem] space-y-1 overflow-y-auto">
                          {filesQuery.isLoading && (
                            <p className="text-sm text-text-muted">
                              Loading files…
                            </p>
                          )}
                          {fileGroups.map((g) => {
                            const fits =
                              freeBytes === null || g.totalBytes === null
                                ? null
                                : g.totalBytes <= freeBytes;
                            return (
                              <div
                                key={g.filename}
                                className="flex items-start justify-between gap-3 rounded-lg px-3 py-2 text-sm hover:bg-background"
                              >
                                <div className="min-w-0">
                                  {/* The whole name, wrapped. Truncating it hid
                                    the one part that differs between two
                                    quantizations of the same repo. */}
                                  <span className="block break-all font-mono text-xs text-text">
                                    {g.label}
                                  </span>
                                  <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                                    <span>{g.quantization}</span>
                                    <span>
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
                                <button
                                  type="button"
                                  onClick={() => handleDownload(g.filename)}
                                  disabled={startDownload.isPending}
                                  title={
                                    g.parts > 1
                                      ? `Download all ${g.parts} parts to ${selectedNode}`
                                      : `Download to ${selectedNode}`
                                  }
                                  aria-label={
                                    g.parts > 1
                                      ? `Download all ${g.parts} parts to ${selectedNode}`
                                      : `Download ${g.label} to ${selectedNode}`
                                  }
                                  className="shrink-0 text-text-muted hover:text-primary disabled:opacity-40"
                                >
                                  <Download size={16} />
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      </>
                    ) : (
                      <div className="flex h-full min-h-[16rem] items-center justify-center text-center text-sm text-text-muted">
                        Select a result to see its files.
                      </div>
                    )}
                  </div>
                </div>

                {/* Only when there is something to show. A third of the width
                  held permanently to say "No downloads yet" was the widest
                  element on a tab whose content had nowhere to go. */}
                {(downloadsQuery.data?.length ?? 0) > 0 && (
                  <div className="mt-6 rounded-xl border border-border bg-surface p-4">
                    <h2 className="mb-3 text-sm font-semibold text-text">
                      Downloads
                    </h2>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {downloadsQuery.data?.map((d) => (
                        <DownloadRow
                          key={d.model_id}
                          entry={d}
                          node={selectedNode}
                        />
                      ))}
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
