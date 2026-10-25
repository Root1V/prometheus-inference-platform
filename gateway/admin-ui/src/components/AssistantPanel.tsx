import { ArrowUp, PanelRightClose, Sparkles, SquarePen } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  askAssistant,
  type AssistantMessage,
  type AssistantReply,
} from "../api/assistant";
import { useAuth } from "../context/AuthContext";
import { getErrorMessage } from "../lib/errors";

/**
 * The dashboard assistant — PRM-272, the "answer" capability.
 *
 * A side panel rather than a page, because the next capability is taking the
 * operator *to* the row it is talking about: on a page of its own, following
 * that link would close the conversation that produced it. Mounted once in
 * App, outside the routes, so it survives navigation; non-modal, so the page
 * underneath stays usable while it is open.
 */

type Turn =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; reply: AssistantReply }
  | { role: "error"; content: string };

type Spoken = Exclude<Turn, { role: "error" }>;

// Questions its four tools can actually answer. An example it cannot answer
// would be the first thing an operator clicks. In Spanish, because that is
// the language it answers in.
const EXAMPLES = [
  "¿Algo necesita atención ahora mismo?",
  "¿Quién ha estado llamando al gateway hoy?",
  "¿Cuánto llevamos gastado este mes y quién gasta más?",
  "¿Algún cliente está cerca de un límite?",
  "¿Quién cambió algo hoy?",
];

// What each tool read, in the operator's words rather than the function name.
const READ: Record<string, string> = {
  list_nodes: "nodes",
  list_instances: "instances",
  list_activity: "activity",
  tail_instance_log: "an instance log",
  get_overview: "the overview",
  list_models: "the model catalog",
  check_limits: "rate limits",
  get_spend: "the bill",
  list_prices: "prices",
  list_audit: "the audit trail",
};

const MOD =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent)
    ? "⌘"
    : "Ctrl+";

// Markdown is styled from the container: the model answers with tables and
// lists, and per-element components would hand react-markdown's `node` prop
// to the DOM. Tables scroll on their own rather than widening the panel.
const PROSE = [
  "text-sm leading-relaxed text-text",
  "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
  "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5",
  "[&_strong]:font-semibold [&_a]:text-primary [&_a]:underline",
  "[&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em]",
  "[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-background [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_table]:my-2 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:border-collapse [&_table]:text-xs",
  "[&_th]:border-b [&_th]:border-border [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium [&_th]:text-text-muted",
  "[&_td]:border-b [&_td]:border-border/60 [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top",
].join(" ");

/**
 * The route the operator is on at the moment they press Enter, so "here"
 * follows them between pages.
 *
 * Read from the URL, not from `useLocation()`. React Router applies a
 * navigation as a transition, and React holds a transition back while urgent
 * updates keep arriving — every keystroke is one. Typing straight after
 * changing page therefore rendered, and sent, the page before: measured, the
 * question asked on Billing went out as /nodes and the one asked on Nodes as
 * /billing, and the model answered faithfully about the wrong page. The hash
 * changes the instant the link is followed.
 */
function currentPage(): string {
  return window.location.hash.replace(/^#/, "").split("?")[0] || "/";
}

/** Rendered only for a signed-in operator, so signing out unmounts it and the
 *  conversation goes with the session rather than outliving it in memory. */
export function AssistantPanel() {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <Panel /> : null;
}

function Panel() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingSince, setPendingSince] = useState<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pending = pendingSince !== null;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [turns, pendingSince, open]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || pending) return;
    // Only what was said travels. An error row is the dashboard talking, not
    // either party, and sending it back would put words in the model's mouth.
    const history: AssistantMessage[] = [
      ...turns
        .filter((t): t is Spoken => t.role !== "error")
        .map(({ role, content }) => ({ role, content })),
      { role: "user", content: question },
    ];
    setTurns((t) => [...t, { role: "user", content: question }]);
    setDraft("");
    setPendingSince(Date.now());
    try {
      const reply = await askAssistant(history, currentPage());
      setTurns((t) => [...t, { role: "assistant", content: reply.content, reply }]);
    } catch (error) {
      setTurns((t) => [...t, { role: "error", content: getErrorMessage(error) }]);
    } finally {
      setPendingSince(null);
      inputRef.current?.focus();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(draft);
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open the assistant"
        title={`Ask the assistant (${MOD}K)`}
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-primary py-2 pl-3.5 pr-2.5 text-sm font-medium text-primary-foreground shadow-lg hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <Sparkles size={15} />
        Ask
        <kbd className="rounded bg-white/20 px-1.5 py-0.5 font-sans text-[11px] font-normal">
          {MOD}K
        </kbd>
      </button>
    );
  }

  return (
    <aside
      aria-label="Assistant"
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[420px] flex-col border-l border-border bg-surface shadow-2xl"
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-text">
            <Sparkles size={15} className="text-primary" />
            Assistant
          </h2>
          <p className="mt-0.5 text-xs text-text-muted">
            Reads the fleet, traffic, limits, spend and the audit trail. It
            cannot change anything yet.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setTurns([])}
            disabled={turns.length === 0 || pending}
            aria-label="New conversation"
            title="New conversation"
            className="rounded-md p-1.5 text-text-muted hover:bg-background hover:text-text disabled:pointer-events-none disabled:opacity-40"
          >
            <SquarePen size={16} />
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Collapse to the button"
            title="Collapse to the button (Esc) — the conversation is kept"
            className="rounded-md p-1.5 text-text-muted hover:bg-background hover:text-text"
          >
            {/* Not an X: collapsing keeps the conversation, and an X reads as
                throwing it away. "New conversation" is the one that does. */}
            <PanelRightClose size={16} />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {turns.length === 0 && !pending ? (
          <div className="flex h-full flex-col justify-end gap-2">
            <p className="mb-1 text-sm text-text-muted">
              Answers come from what the platform reports right now, not from
              memory.
            </p>
            {EXAMPLES.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => void send(q)}
                className="rounded-lg border border-border px-3 py-2 text-left text-sm text-text hover:border-primary/60 hover:bg-primary/5"
              >
                {q}
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-5" aria-live="polite">
            {turns.map((turn, i) => (
              <TurnView key={i} turn={turn} />
            ))}
            {pendingSince !== null && <Thinking since={pendingSince} />}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <form onSubmit={onSubmit} className="border-t border-border p-3">
        <div className="flex items-end gap-2 rounded-xl border border-border bg-background px-3 py-2 focus-within:border-primary/60">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send(draft);
              }
            }}
            rows={2}
            placeholder="Ask about the fleet…"
            aria-label="Your question"
            className="max-h-40 flex-1 resize-none bg-transparent text-sm text-text outline-none [field-sizing:content] placeholder:text-text-muted"
          />
          <button
            type="submit"
            disabled={!draft.trim() || pending}
            aria-label="Send"
            className="mb-0.5 rounded-lg bg-primary p-1.5 text-primary-foreground hover:opacity-90 disabled:opacity-40"
          >
            <ArrowUp size={16} />
          </button>
        </div>
        <p className="mt-1.5 px-1 text-[11px] text-text-muted">
          Enter to send · Shift+Enter for a new line · Esc to collapse
        </p>
      </form>
    </aside>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary/10 px-3.5 py-2 text-sm text-text">
          {turn.content}
        </p>
      </div>
    );
  }
  if (turn.role === "error") {
    return (
      <div
        role="alert"
        className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300"
      >
        {turn.content}
      </div>
    );
  }
  const read = [...new Set(turn.reply.tool_calls.map((t) => READ[t] ?? t))];
  return (
    <div>
      <div className={PROSE}>
        <Markdown remarkPlugins={[remarkGfm]}>{turn.content}</Markdown>
      </div>
      {/* Which reads stand behind the answer. One with none came from the
          model alone, which is worth seeing when it is meant to report. */}
      <p className="mt-1.5 text-[11px] tabular-nums text-text-muted">
        {read.length > 0
          ? `Read ${read.join(", ")}`
          : "Answered without reading anything"}
        {` · ${turn.reply.elapsed_s.toFixed(1)} s`}
      </p>
    </div>
  );
}

/** No streaming, so the wait is visible instead: a slow local model with a
 *  tool round-trip can take tens of seconds, and a spinner that never says
 *  how long reads as a hang. */
function Thinking({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return (
    <div className="flex items-center gap-2 text-xs text-text-muted">
      <span className="flex gap-1" aria-hidden="true">
        <span className="h-1 w-1 animate-bounce-dot rounded-full bg-current" />
        <span
          className="h-1 w-1 animate-bounce-dot rounded-full bg-current"
          style={{ animationDelay: "200ms" }}
        />
        <span
          className="h-1 w-1 animate-bounce-dot rounded-full bg-current"
          style={{ animationDelay: "400ms" }}
        />
      </span>
      Reading the platform{seconds >= 2 ? ` · ${seconds} s` : "…"}
    </div>
  );
}
