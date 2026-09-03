import { Copy, Download, RotateCcw, Send, Trash2, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useInstances } from "../api/instances";
import {
  streamPlaygroundChat,
  useEmbeddings,
  useImageGenerations,
  usePlaygroundChat,
  type ChatMessage,
  type ToolCall,
  type ToolDefinition,
} from "../api/playground";
import { Sidebar } from "../components/Sidebar";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";

/** Auto-scrolls to its own bottom as `text` grows — so the model's live chain-of-thought
 * stays visible instead of scrolling out of a fixed-height box (RM-36 follow-up). */
function ReasoningBox({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "end" });
  }, [text]);
  return (
    <div className="max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted">
      <p className="mb-1 font-medium">🧠 Thinking…</p>
      <div className="max-h-40 overflow-y-auto">
        <p className="whitespace-pre-wrap italic">
          {text}
          <span className="animate-pulse">▍</span>
        </p>
        <div ref={ref} />
      </div>
    </div>
  );
}

/** RM-42: a static "Waiting…" string reads as stalled during a real
 * multi-second inference call — three dots pulsing in sequence signal it's
 * still actively working. */
function WaitingIndicator({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-1.5 text-sm text-text-muted">
      {label}
      <span className="flex items-center gap-0.5">
        <span
          className="h-1 w-1 animate-bounce-dot rounded-full bg-current"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="h-1 w-1 animate-bounce-dot rounded-full bg-current"
          style={{ animationDelay: "150ms" }}
        />
        <span
          className="h-1 w-1 animate-bounce-dot rounded-full bg-current"
          style={{ animationDelay: "300ms" }}
        />
      </span>
    </p>
  );
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

interface Turn {
  // Leading message(s) — either one "user" message, or one "tool" message per
  // pending tool_call being answered — followed by the assistant's response.
  messages: ChatMessage[];
  // null for a streamed response — the backends verified so far don't report
  // usage for stream:true (docs/roadmap.md RM-36).
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
  latencyMs: number;
  finishReason: string | null;
  // The model's chain-of-thought for this turn, if any — kept around (collapsed
  // in the UI) rather than discarded, especially useful when it ran out of
  // max_tokens before producing a visible answer.
  reasoning: string;
  // RM-41: which model produced this turn's response — captured at send time,
  // so switching models mid-conversation still shows the right label on each
  // earlier turn instead of relying on the (possibly since-changed) current
  // selection.
  model: string;
}

interface InProgress {
  leading: ChatMessage[];
  content: string;
  reasoning: string;
  toolCalls: ToolCall[];
}

interface EmbeddingResult {
  input: string;
  embedding: number[];
  usage: { prompt_tokens: number; total_tokens: number };
  latencyMs: number;
  model: string;
}

interface ImageResult {
  prompt: string;
  b64Json: string;
  latencyMs: number;
  model: string;
}

const EMBEDDING_PREVIEW_COUNT = 8;

export default function Playground() {
  const instancesQuery = useInstances();
  const chat = usePlaygroundChat();
  const embeddings = useEmbeddings();
  const imageGenerations = useImageGenerations();

  const [mode, setMode] = useState<"chat" | "embeddings" | "images">("chat");
  const [embedModel, setEmbedModel] = useState("");
  const [embedInput, setEmbedInput] = useState("");
  const [embedResults, setEmbedResults] = useState<EmbeddingResult[]>([]);
  const [embedError, setEmbedError] = useState<string | null>(null);

  const [imageModel, setImageModel] = useState("");
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageResults, setImageResults] = useState<ImageResult[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [expandedImage, setExpandedImage] = useState<ImageResult | null>(null);

  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);

  const [temperature, setTemperature] = useState(1.0);
  const [topP, setTopP] = useState(1.0);
  const [maxTokens, setMaxTokens] = useState(512);
  const [stopInput, setStopInput] = useState("");
  const [toolsInput, setToolsInput] = useState("");
  const [toolChoice, setToolChoice] = useState<"auto" | "required" | "none">("auto");
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [toolResultDrafts, setToolResultDrafts] = useState<Record<string, string>>({});
  const [streamingEnabled, setStreamingEnabled] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [inProgress, setInProgress] = useState<InProgress | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!expandedImage) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpandedImage(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expandedImage]);

  const runningTextModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "text",
  );
  const selectedModel = model || runningTextModels[0]?.id || "";

  const runningEmbeddingModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "embedding",
  );
  const selectedEmbedModel = embedModel || runningEmbeddingModels[0]?.id || "";

  const runningImageModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "image",
  );
  const selectedImageModel = imageModel || runningImageModels[0]?.id || "";

  async function handleGetEmbedding() {
    if (!selectedEmbedModel || !embedInput.trim() || embeddings.isPending) return;
    setEmbedError(null);
    // Captured before clearing so the result still records what was actually
    // sent, and cleared immediately (not on success) so the input reads as
    // sent right away instead of lingering, editable, during the wait.
    const input = embedInput;
    setEmbedInput("");
    const startedAt = performance.now();
    try {
      const data = await embeddings.mutateAsync({ model: selectedEmbedModel, input });
      setEmbedResults((prev) => [
        ...prev,
        {
          input,
          embedding: data.data[0]?.embedding ?? [],
          usage: data.usage,
          latencyMs: Math.round(performance.now() - startedAt),
          model: selectedEmbedModel,
        },
      ]);
    } catch (error) {
      setEmbedError(getErrorMessage(error));
    }
  }

  async function handleCopyEmbedding(embedding: number[]) {
    await navigator.clipboard.writeText(JSON.stringify(embedding));
  }

  async function handleGenerateImage() {
    if (!selectedImageModel || !imagePrompt.trim() || imageGenerations.isPending) return;
    setImageError(null);
    // Same rationale as handleGetEmbedding: capture before clearing so the
    // result still records the real prompt, and clear immediately rather
    // than on success so the input reads as sent right away.
    const prompt = imagePrompt;
    setImagePrompt("");
    const startedAt = performance.now();
    try {
      const data = await imageGenerations.mutateAsync({ model: selectedImageModel, prompt });
      setImageResults((prev) => [
        ...prev,
        {
          prompt,
          b64Json: data.data[0]?.b64_json ?? "",
          latencyMs: Math.round(performance.now() - startedAt),
          model: selectedImageModel,
        },
      ]);
    } catch (error) {
      setImageError(getErrorMessage(error));
    }
  }

  function handleDownloadImage(result: ImageResult, index: number) {
    const link = document.createElement("a");
    link.href = `data:image/png;base64,${result.b64Json}`;
    link.download = `prometheus-image-${index + 1}.png`;
    link.click();
  }

  /** Returns null (and sets toolsError) if the JSON is present but invalid. */
  function parseTools(): ToolDefinition[] | null | undefined {
    if (!toolsInput.trim()) {
      setToolsError(null);
      return undefined;
    }
    try {
      const parsed = JSON.parse(toolsInput);
      if (!Array.isArray(parsed)) throw new Error("Tools must be a JSON array.");
      setToolsError(null);
      return parsed as ToolDefinition[];
    } catch (e) {
      setToolsError(e instanceof Error ? e.message : "Invalid JSON.");
      return null;
    }
  }

  function buildParams(tools: ToolDefinition[] | undefined) {
    const stop = stopInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
      ...(stop.length > 0 ? { stop } : {}),
      ...(tools ? { tools, tool_choice: toolChoice } : {}),
    };
  }

  function historyMessages(): ChatMessage[] {
    const history = turns.flatMap((t) => t.messages);
    return systemPrompt.trim()
      ? [{ role: "system" as const, content: systemPrompt }, ...history]
      : history;
  }

  async function sendNonStreaming(leadingMessages: ChatMessage[], messages: ChatMessage[], tools: ToolDefinition[] | undefined) {
    const startedAt = performance.now();
    // Show the user's own message immediately instead of leaving it
    // invisible until the whole round-trip completes — same treatment the
    // streaming path already gives it via inProgress.leading.
    setInProgress({ leading: leadingMessages, content: "", reasoning: "", toolCalls: [] });
    const data = await chat.mutateAsync({ model: selectedModel, messages, params: buildParams(tools) });
    const latencyMs = Math.round(performance.now() - startedAt);
    const responseMessage = data.choices[0]?.message;
    const hasToolCalls = (responseMessage?.tool_calls?.length ?? 0) > 0;
    const assistantMessage: ChatMessage = {
      role: "assistant",
      // Same rule as the streaming path: null content is only valid when
      // tool_calls carries the payload instead, or the backend never gets
      // sent as history on a later turn.
      content: hasToolCalls ? null : (responseMessage?.content ?? ""),
      tool_calls: responseMessage?.tool_calls,
    };
    setInProgress(null);
    setTurns((prev) => [
      ...prev,
      {
        messages: [...leadingMessages, assistantMessage],
        usage: data.usage,
        latencyMs,
        finishReason: data.choices[0]?.finish_reason ?? null,
        reasoning: responseMessage?.reasoning_content ?? "",
        model: selectedModel,
      },
    ]);
  }

  async function sendStreaming(leadingMessages: ChatMessage[], messages: ChatMessage[], tools: ToolDefinition[] | undefined) {
    const startedAt = performance.now();
    let content = "";
    let reasoning = "";
    const toolCallsByIndex = new Map<number, ToolCall>();
    setInProgress({ leading: leadingMessages, content: "", reasoning: "", toolCalls: [] });

    const { finishReason, usage } = await streamPlaygroundChat(
      { model: selectedModel, messages, params: buildParams(tools) },
      (delta) => {
        if (delta.content) content += delta.content;
        if (delta.reasoning_content) reasoning += delta.reasoning_content;
        for (const partial of delta.tool_calls ?? []) {
          const existing = toolCallsByIndex.get(partial.index) ?? {
            id: "",
            type: "function" as const,
            function: { name: "", arguments: "" },
          };
          if (partial.id) existing.id = partial.id;
          if (partial.function?.name) existing.function.name = partial.function.name;
          if (partial.function?.arguments) existing.function.arguments += partial.function.arguments;
          toolCallsByIndex.set(partial.index, existing);
        }
        setInProgress({ leading: leadingMessages, content, reasoning, toolCalls: [...toolCallsByIndex.values()] });
        requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
      },
    );

    const latencyMs = Math.round(performance.now() - startedAt);
    const toolCalls = [...toolCallsByIndex.values()];
    const assistantMessage: ChatMessage = {
      role: "assistant",
      // OpenAI's shape only allows null content when tool_calls carries the
      // payload instead — an assistant message with neither is invalid and
      // gets the *entire conversation* rejected by the backend on every
      // later turn. An empty-but-present string (e.g. ran out of max_tokens
      // during reasoning) must stay a string, never null.
      content: toolCalls.length > 0 ? null : content,
      tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
    };
    setInProgress(null);
    setTurns((prev) => [
      ...prev,
      { messages: [...leadingMessages, assistantMessage], usage, latencyMs, finishReason, reasoning, model: selectedModel },
    ]);
  }

  async function sendMessages(leadingMessages: ChatMessage[]) {
    if (!selectedModel || isSending) return;
    const tools = parseTools();
    if (tools === null) return; // invalid JSON — toolsError is already set
    setSendError(null);
    setIsSending(true);
    const messages = [...historyMessages(), ...leadingMessages];
    try {
      if (streamingEnabled) {
        await sendStreaming(leadingMessages, messages, tools);
      } else {
        await sendNonStreaming(leadingMessages, messages, tools);
      }
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
    } catch (error) {
      setInProgress(null);
      setSendError(getErrorMessage(error));
    } finally {
      setIsSending(false);
    }
  }

  function handleSend() {
    if (!draft.trim() || isSending) return;
    const userMessage: ChatMessage = { role: "user", content: draft };
    setDraft("");
    void sendMessages([userMessage]);
  }

  function handleRegenerate() {
    if (turns.length === 0 || isSending) return;
    const lastLeading = turns[turns.length - 1].messages.slice(0, -1);
    setTurns((prev) => prev.slice(0, -1));
    void sendMessages(lastLeading);
  }

  /** RM-35 follow-up: the model asked to call a tool — the Playground has no real
   * tool executor, so the operator types a mock result per call to continue the
   * conversation and see the model's actual final answer. */
  function handleSubmitToolResults(calls: ChatMessage["tool_calls"]) {
    if (!calls || calls.length === 0 || isSending) return;
    const toolMessages: ChatMessage[] = calls.map((call) => ({
      role: "tool",
      tool_call_id: call.id,
      content: toolResultDrafts[call.id]?.trim() || "(no result provided)",
    }));
    setToolResultDrafts({});
    void sendMessages(toolMessages);
  }

  function handleClear() {
    if (mode === "chat") {
      setTurns([]);
      setSendError(null);
    } else if (mode === "embeddings") {
      setEmbedResults([]);
      setEmbedError(null);
    } else {
      setImageResults([]);
      setImageError(null);
    }
  }

  async function handleCopy(message: ChatMessage) {
    const text = message.content ?? JSON.stringify(message.tool_calls, null, 2);
    await navigator.clipboard.writeText(text ?? "");
  }

  async function handleCopyReasoning(reasoning: string) {
    await navigator.clipboard.writeText(reasoning);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex min-h-0 min-w-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col px-8 py-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-text">Playground</h1>
              <p className="mt-1 text-sm text-text-muted">
                Sends real requests through the gateway's inference API — this counts as
                real usage, recorded the same as any other call.
              </p>
            </div>
            <button
              type="button"
              onClick={handleClear}
              disabled={
                mode === "chat"
                  ? turns.length === 0
                  : mode === "embeddings"
                    ? embedResults.length === 0
                    : imageResults.length === 0
              }
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 size={14} />
              Clear
            </button>
          </div>

          <div className="mt-4 flex gap-2 border-b border-border">
            <button
              type="button"
              onClick={() => setMode("chat")}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium",
                mode === "chat"
                  ? "border-primary text-text"
                  : "border-transparent text-text-muted hover:text-text",
              )}
            >
              Chat
            </button>
            <button
              type="button"
              onClick={() => setMode("embeddings")}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium",
                mode === "embeddings"
                  ? "border-primary text-text"
                  : "border-transparent text-text-muted hover:text-text",
              )}
            >
              Embeddings
            </button>
            <button
              type="button"
              onClick={() => setMode("images")}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium",
                mode === "images"
                  ? "border-primary text-text"
                  : "border-transparent text-text-muted hover:text-text",
              )}
            >
              Images
            </button>
          </div>

          {mode === "chat" ? (
          <>
          <div className="mt-4">
            <label htmlFor="playground-system" className="mb-1.5 block text-sm font-medium text-text">
              System prompt
            </label>
            <textarea
              id="playground-system"
              rows={2}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Optional — sets the assistant's behavior for this conversation."
              className={inputClass}
            />
          </div>

          <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto rounded-xl border border-border bg-surface p-4">
            {turns.length === 0 ? (
              <p className="text-sm text-text-muted">No messages yet — send a prompt to get started.</p>
            ) : (
              turns.map((turn, i) => {
                const leading = turn.messages.slice(0, -1);
                const assistantMessage = turn.messages[turn.messages.length - 1];
                const isLastTurn = i === turns.length - 1;
                return (
                <div key={i} className="space-y-3">
                  {leading.map((m, j) =>
                    m.role === "tool" ? (
                      <div
                        key={j}
                        className="ml-auto max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted"
                      >
                        Tool result: {m.content}
                      </div>
                    ) : (
                      <div
                        key={j}
                        className="ml-auto max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground"
                      >
                        {m.content}
                      </div>
                    ),
                  )}
                  <div className="max-w-[80%] rounded-xl border border-border bg-background px-4 py-2 text-sm text-text">
                    {assistantMessage.content && (
                      <p className="whitespace-pre-wrap">{assistantMessage.content}</p>
                    )}
                    {!assistantMessage.content &&
                      !assistantMessage.tool_calls?.length &&
                      turn.finishReason === "length" && (
                        <p className="text-amber-600">
                          Ran out of max tokens before producing a visible answer — this model
                          spends tokens on hidden reasoning first, and used up the whole budget
                          there. Try raising Max tokens.
                        </p>
                      )}
                    {turn.reasoning && (
                      <details className="mt-2 rounded-lg border border-dashed border-border bg-surface p-2 text-xs text-text-muted">
                        <summary className="cursor-pointer select-none font-medium">
                          Show the model's reasoning ({turn.reasoning.length.toLocaleString()} chars)
                        </summary>
                        <div className="mt-2 flex items-start justify-between gap-2">
                          <p className="max-h-64 overflow-y-auto whitespace-pre-wrap italic">
                            {turn.reasoning}
                          </p>
                          <button
                            type="button"
                            onClick={() => handleCopyReasoning(turn.reasoning)}
                            title="Copy reasoning"
                            className="shrink-0 text-text-muted hover:text-text"
                          >
                            <Copy size={14} />
                          </button>
                        </div>
                      </details>
                    )}
                    {assistantMessage.tool_calls?.map((call) => (
                      <div
                        key={call.id}
                        className="mt-1 flex items-start gap-2 rounded-lg bg-surface p-2 font-mono text-xs text-text"
                      >
                        <Wrench size={14} className="mt-0.5 shrink-0 text-primary" />
                        <div>
                          <span className="font-semibold">{call.function.name}</span>
                          <span className="text-text-muted">({call.function.arguments})</span>
                        </div>
                      </div>
                    ))}
                    <div className="mt-2 flex items-center gap-3 border-t border-border pt-2 text-xs text-text-muted">
                      <span>
                        {turn.usage
                          ? `${turn.usage.prompt_tokens} + ${turn.usage.completion_tokens} = ${turn.usage.total_tokens} tokens`
                          : "tokens not reported (streamed)"}
                      </span>
                      <span>{turn.latencyMs} ms</span>
                      <span className="ml-auto font-mono" title="Model that answered this turn">
                        {turn.model}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleCopy(assistantMessage)}
                        title="Copy response"
                        className="text-text-muted hover:text-text"
                      >
                        <Copy size={14} />
                      </button>
                      {isLastTurn && (
                        <button
                          type="button"
                          onClick={handleRegenerate}
                          disabled={isSending}
                          title="Regenerate"
                          className="text-text-muted hover:text-text disabled:opacity-40"
                        >
                          <RotateCcw size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                  {isLastTurn && assistantMessage.tool_calls && assistantMessage.tool_calls.length > 0 && (
                    <div className="max-w-[80%] space-y-2 rounded-xl border border-dashed border-border bg-surface p-3">
                      <p className="text-xs text-text-muted">
                        The Playground doesn't execute tools for real — type a mock result to
                        continue and see the model's final answer.
                      </p>
                      {assistantMessage.tool_calls.map((call) => (
                        <div key={call.id} className="flex items-center gap-2">
                          <span className="shrink-0 font-mono text-xs text-text-muted">
                            {call.function.name}:
                          </span>
                          <input
                            type="text"
                            value={toolResultDrafts[call.id] ?? ""}
                            onChange={(e) =>
                              setToolResultDrafts((prev) => ({ ...prev, [call.id]: e.target.value }))
                            }
                            placeholder="Mock result, e.g. 22C, sunny"
                            className={cn(inputClass, "text-xs")}
                          />
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={() => handleSubmitToolResults(assistantMessage.tool_calls)}
                        disabled={isSending}
                        className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Submit result
                      </button>
                    </div>
                  )}
                </div>
                );
              })
            )}
            {inProgress &&
              inProgress.leading.map((m, j) =>
                m.role === "tool" ? (
                  <div
                    key={j}
                    className="ml-auto max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted"
                  >
                    Tool result: {m.content}
                  </div>
                ) : (
                  <div
                    key={j}
                    className="ml-auto max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground"
                  >
                    {m.content}
                  </div>
                ),
              )}
            {inProgress &&
              !inProgress.content &&
              inProgress.toolCalls.length === 0 &&
              inProgress.reasoning && <ReasoningBox text={inProgress.reasoning} />}
            {inProgress && (inProgress.content || inProgress.toolCalls.length > 0) && (
              <div className="max-w-[80%] rounded-xl border border-border bg-background px-4 py-2 text-sm text-text">
                {inProgress.content && (
                  <p className="whitespace-pre-wrap">
                    {inProgress.content}
                    <span className="animate-pulse">▍</span>
                  </p>
                )}
                {inProgress.toolCalls.map((call, idx) => (
                  <div
                    key={idx}
                    className="mt-1 flex items-start gap-2 rounded-lg bg-surface p-2 font-mono text-xs text-text"
                  >
                    <Wrench size={14} className="mt-0.5 shrink-0 text-primary" />
                    <div>
                      <span className="font-semibold">{call.function.name || "…"}</span>
                      <span className="text-text-muted">({call.function.arguments})</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {inProgress && !inProgress.content && !inProgress.reasoning && inProgress.toolCalls.length === 0 && (
              <WaitingIndicator label={streamingEnabled ? "Streaming" : "Waiting for a response"} />
            )}
            <div ref={bottomRef} />
          </div>

          {sendError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
              {sendError}
            </div>
          )}

          <div className="mt-4 flex items-end gap-2">
            <textarea
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                runningTextModels.length === 0
                  ? "No running text models — start one from Instances first."
                  : "Ask something… (Enter to send, Shift+Enter for a new line)"
              }
              disabled={runningTextModels.length === 0 || isSending}
              className={cn(inputClass, "flex-1")}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!selectedModel || !draft.trim() || isSending}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={16} />
              Send
            </button>
          </div>
          </>
          ) : mode === "embeddings" ? (
          <>
          <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto rounded-xl border border-border bg-surface p-4">
            {embedResults.length === 0 && !embeddings.isPending ? (
              <p className="text-sm text-text-muted">
                No embeddings yet — send some text to get its vector.
              </p>
            ) : (
              <>
                {embedResults.map((result, i) => (
                  <div key={i} className="space-y-3">
                    <div className="ml-auto w-fit max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground">
                      {result.input}
                    </div>
                    <div className="rounded-xl border border-border bg-background px-4 py-3 text-sm text-text">
                      <p className="font-mono text-xs text-text">
                        [{result.embedding
                          .slice(0, EMBEDDING_PREVIEW_COUNT)
                          .map((v) => v.toFixed(4))
                          .join(", ")}
                        {result.embedding.length > EMBEDDING_PREVIEW_COUNT ? ", …" : ""}]
                      </p>
                      <div className="mt-2 flex items-center gap-3 border-t border-border pt-2 text-xs text-text-muted">
                        <span>{result.embedding.length} dimensions</span>
                        <span>{result.usage.total_tokens} tokens</span>
                        <span>{result.latencyMs} ms</span>
                        <span className="ml-auto font-mono" title="Model that produced this embedding">
                          {result.model}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleCopyEmbedding(result.embedding)}
                          title="Copy full vector as JSON"
                          className="text-text-muted hover:text-text"
                        >
                          <Copy size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
                {embeddings.isPending && <WaitingIndicator label="Generating embedding" />}
              </>
            )}
          </div>

          {embedError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
              {embedError}
            </div>
          )}

          <div className="mt-4 flex items-end gap-2">
            <textarea
              rows={2}
              value={embedInput}
              onChange={(e) => setEmbedInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleGetEmbedding();
                }
              }}
              placeholder={
                runningEmbeddingModels.length === 0
                  ? "No running embedding models — start one from Instances first."
                  : "Text to embed… (Enter to send, Shift+Enter for a new line)"
              }
              disabled={runningEmbeddingModels.length === 0 || embeddings.isPending}
              className={cn(inputClass, "flex-1")}
            />
            <button
              type="button"
              onClick={() => void handleGetEmbedding()}
              disabled={!selectedEmbedModel || !embedInput.trim() || embeddings.isPending}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={16} />
              Get embedding
            </button>
          </div>
          </>
          ) : (
          <>
          <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto rounded-xl border border-border bg-surface p-4">
            {imageResults.length === 0 && !imageGenerations.isPending ? (
              <p className="text-sm text-text-muted">
                No images yet — send a prompt to generate one.
              </p>
            ) : (
              <>
                {imageResults.map((result, i) => (
                  <div key={i} className="space-y-3">
                    <div className="ml-auto w-fit max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground">
                      {result.prompt}
                    </div>
                    <div className="max-w-xs rounded-xl border border-border bg-background px-4 py-3 text-sm text-text">
                      <button
                        type="button"
                        onClick={() => setExpandedImage(result)}
                        className="block cursor-zoom-in"
                        title="Click to view full size"
                      >
                        <img
                          src={`data:image/png;base64,${result.b64Json}`}
                          alt={result.prompt}
                          className="w-full rounded-lg border border-border"
                        />
                      </button>
                      <div className="mt-2 flex items-center gap-3 border-t border-border pt-2 text-xs text-text-muted">
                        <span className="shrink-0">{result.latencyMs} ms</span>
                        <span
                          className="ml-auto truncate font-mono"
                          title={`Model that generated this image: ${result.model}`}
                        >
                          {result.model}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleDownloadImage(result, i)}
                          title="Download image"
                          className="shrink-0 cursor-pointer text-text-muted hover:text-text"
                        >
                          <Download size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
                {imageGenerations.isPending && <WaitingIndicator label="Generating image" />}
              </>
            )}
          </div>

          {imageError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
              {imageError}
            </div>
          )}

          <div className="mt-4 flex items-end gap-2">
            <textarea
              rows={2}
              value={imagePrompt}
              onChange={(e) => setImagePrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleGenerateImage();
                }
              }}
              placeholder={
                runningImageModels.length === 0
                  ? "No running image models — start one from Instances first."
                  : "Describe the image to generate… (Enter to send, Shift+Enter for a new line)"
              }
              disabled={runningImageModels.length === 0 || imageGenerations.isPending}
              className={cn(inputClass, "flex-1")}
            />
            <button
              type="button"
              onClick={() => void handleGenerateImage()}
              disabled={!selectedImageModel || !imagePrompt.trim() || imageGenerations.isPending}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={16} />
              Generate
            </button>
          </div>
          </>
          )}
        </div>

        <aside className="w-72 shrink-0 space-y-5 overflow-y-auto border-l border-border bg-surface px-5 py-8">
          {mode === "chat" ? (
          <>
          <div>
            <label htmlFor="playground-model" className="mb-1.5 block text-sm font-medium text-text">
              Model
            </label>
            {runningTextModels.length === 0 ? (
              <p className="text-sm text-text-muted">No running text models.</p>
            ) : (
              <select
                id="playground-model"
                value={selectedModel}
                onChange={(e) => setModel(e.target.value)}
                className={inputClass}
              >
                {runningTextModels.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}
                  </option>
                ))}
              </select>
            )}
          </div>

          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={streamingEnabled}
              onChange={(e) => setStreamingEnabled(e.target.checked)}
            />
            Stream response
          </label>

          <h2 className="text-xs font-medium uppercase tracking-wide text-text-muted">Parameters</h2>

          <div>
            <div className="mb-1.5 flex items-center justify-between text-sm text-text">
              <label htmlFor="playground-temperature">Temperature</label>
              <span className="text-text-muted">{temperature.toFixed(1)}</span>
            </div>
            <input
              id="playground-temperature"
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
              className="w-full"
            />
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between text-sm text-text">
              <label htmlFor="playground-top-p">Top P</label>
              <span className="text-text-muted">{topP.toFixed(2)}</span>
            </div>
            <input
              id="playground-top-p"
              type="range"
              min={0.05}
              max={1}
              step={0.05}
              value={topP}
              onChange={(e) => setTopP(Number(e.target.value))}
              className="w-full"
            />
          </div>

          <div>
            <label htmlFor="playground-max-tokens" className="mb-1.5 block text-sm text-text">
              Max tokens
            </label>
            <input
              id="playground-max-tokens"
              type="number"
              min={1}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Math.max(1, Number(e.target.value) || 1))}
              className={inputClass}
            />
          </div>

          <div>
            <label htmlFor="playground-stop" className="mb-1.5 block text-sm text-text">
              Stop sequences
            </label>
            <input
              id="playground-stop"
              type="text"
              value={stopInput}
              onChange={(e) => setStopInput(e.target.value)}
              placeholder="Comma-separated, optional"
              className={inputClass}
            />
          </div>

          <h2 className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Tools (function calling)
          </h2>

          <div>
            <label htmlFor="playground-tools" className="mb-1.5 block text-sm text-text">
              Tool definitions (JSON)
            </label>
            <textarea
              id="playground-tools"
              rows={6}
              value={toolsInput}
              onChange={(e) => setToolsInput(e.target.value)}
              placeholder={'Optional — an OpenAI-style tools array, e.g.\n[\n  {\n    "type": "function",\n    "function": {\n      "name": "get_weather",\n      "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}\n    }\n  }\n]'}
              className={cn(inputClass, "font-mono text-xs")}
            />
            {toolsError && <p className="mt-1 text-xs text-red-600">{toolsError}</p>}
          </div>

          <div>
            <label htmlFor="playground-tool-choice" className="mb-1.5 block text-sm text-text">
              Tool choice
            </label>
            <select
              id="playground-tool-choice"
              value={toolChoice}
              onChange={(e) => setToolChoice(e.target.value as "auto" | "required" | "none")}
              disabled={!toolsInput.trim()}
              className={cn(inputClass, !toolsInput.trim() && "opacity-40")}
            >
              <option value="auto">auto</option>
              <option value="required">required</option>
              <option value="none">none</option>
            </select>
            {toolChoice === "required" && (
              <p className="mt-1 text-xs text-text-muted">
                "required" forces a tool call every turn — switch to "auto" after submitting a
                result if you want the model's final text answer instead of another call.
              </p>
            )}
          </div>
          </>
          ) : mode === "embeddings" ? (
          <div>
            <label htmlFor="playground-embed-model" className="mb-1.5 block text-sm font-medium text-text">
              Model
            </label>
            {runningEmbeddingModels.length === 0 ? (
              <p className="text-sm text-text-muted">No running embedding models.</p>
            ) : (
              <select
                id="playground-embed-model"
                value={selectedEmbedModel}
                onChange={(e) => setEmbedModel(e.target.value)}
                className={inputClass}
              >
                {runningEmbeddingModels.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}
                  </option>
                ))}
              </select>
            )}
            <p className="mt-3 text-xs text-text-muted">
              Embeddings are single-shot — each request is independent, there's no
              conversation history to carry over between them.
            </p>
          </div>
          ) : (
          <div>
            <label htmlFor="playground-image-model" className="mb-1.5 block text-sm font-medium text-text">
              Model
            </label>
            {runningImageModels.length === 0 ? (
              <p className="text-sm text-text-muted">No running image models.</p>
            ) : (
              <select
                id="playground-image-model"
                value={selectedImageModel}
                onChange={(e) => setImageModel(e.target.value)}
                className={inputClass}
              >
                {runningImageModels.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}
                  </option>
                ))}
              </select>
            )}
            <p className="mt-3 text-xs text-text-muted">
              Each prompt is single-shot — there's no conversation history to carry over
              between generations.
            </p>
          </div>
          )}
        </aside>
      </main>
      {expandedImage &&
        createPortal(
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-8"
            onClick={() => setExpandedImage(null)}
          >
            <div className="absolute right-6 top-6 flex items-center gap-4">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDownloadImage(expandedImage, imageResults.indexOf(expandedImage));
                }}
                aria-label="Download"
                title="Download image"
                className="cursor-pointer text-white/80 hover:text-white"
              >
                <Download size={24} />
              </button>
              <button
                type="button"
                onClick={() => setExpandedImage(null)}
                aria-label="Close"
                className="cursor-pointer text-white/80 hover:text-white"
              >
                <X size={28} />
              </button>
            </div>
            <img
              src={`data:image/png;base64,${expandedImage.b64Json}`}
              alt={expandedImage.prompt}
              onClick={(e) => e.stopPropagation()}
              className="max-h-full max-w-full rounded-lg object-contain"
            />
          </div>,
          document.body,
        )}
    </div>
  );
}
