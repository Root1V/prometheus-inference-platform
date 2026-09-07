import { useMutation } from "@tanstack/react-query";
import { getStoredToken } from "./auth";
import { AUTH_EXPIRED_EVENT, rootClient } from "./client";

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

/** RM-40: the gateway's /v1/chat/completions accepts an array of content
 * parts instead of a plain string — this is how an image rides alongside
 * the text prompt for a vision-capable model. Images must be inline data:
 * URIs (gateway/src/prometheus_gateway/models/schemas.py rejects remote
 * http(s) URLs to avoid turning the endpoint into an SSRF proxy). */
export interface TextContentPart {
  type: "text";
  text: string;
}

export interface ImageContentPart {
  type: "image_url";
  image_url: { url: string };
}

export type ContentPart = TextContentPart | ImageContentPart;

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | ContentPart[] | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface ToolDefinition {
  type: "function";
  function: { name: string; description?: string; parameters?: object };
}

export interface PlaygroundParams {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string[];
  tools?: ToolDefinition[];
  tool_choice?: string;
}

interface ChatCompletionResponse {
  choices: {
    message: {
      role: string;
      content: string | null;
      tool_calls?: ToolCall[];
      /** gpt-oss-style chain-of-thought — not part of the OpenAI response schema,
       * but some backends include it. Kept for the "ran out of tokens" case. */
      reasoning_content?: string;
    };
    finish_reason: string | null;
  }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

/**
 * RM-14: calls the gateway's real POST /v1/chat/completions with the admin
 * dashboard's own session token — the same endpoint real clients use, so
 * usage/cost recording and metrics all apply exactly as they would for any
 * other caller.
 */
export function usePlaygroundChat() {
  return useMutation({
    mutationFn: async ({
      model,
      messages,
      params,
    }: {
      model: string;
      messages: ChatMessage[];
      params: PlaygroundParams;
    }) =>
      (
        await rootClient.post<ChatCompletionResponse>("/v1/chat/completions", {
          model,
          messages,
          stream: false,
          ...params,
        })
      ).data,
  });
}

interface EmbeddingsResponse {
  object: string;
  data: { object: string; index: number; embedding: number[] }[];
  model: string;
  usage: { prompt_tokens: number; total_tokens: number };
}

/**
 * RM-37: calls the gateway's real POST /v1/embeddings with the admin
 * dashboard's own session token — same real-usage rationale as
 * usePlaygroundChat (RM-14).
 */
export function useEmbeddings() {
  return useMutation({
    mutationFn: async ({ model, input }: { model: string; input: string }) =>
      (await rootClient.post<EmbeddingsResponse>("/v1/embeddings", { model, input })).data,
  });
}

interface ImageGenerationResponse {
  created: number;
  data: { b64_json: string }[];
  output_format?: string;
}

/**
 * RM-38: calls the gateway's real POST /v1/images/generations with the admin
 * dashboard's own session token — same real-usage rationale as
 * usePlaygroundChat (RM-14) and useEmbeddings (RM-37).
 */
export function useImageGenerations() {
  return useMutation({
    mutationFn: async ({ model, prompt }: { model: string; prompt: string }) =>
      (await rootClient.post<ImageGenerationResponse>("/v1/images/generations", { model, prompt }))
        .data,
  });
}

/** A partial tool_calls entry as it arrives in a streaming delta — same shape
 * OpenAI/llama.cpp use: only `index` is guaranteed on every chunk; `id`/
 * `function.name` arrive once, `function.arguments` arrives in fragments. */
export interface ToolCallDelta {
  index: number;
  id?: string;
  function?: { name?: string; arguments?: string };
}

export interface StreamDelta {
  content?: string;
  /** gpt-oss-style chain-of-thought, sent as its own delta field before any
   * real `content` — surfaced live so a long reasoning phase doesn't look
   * frozen (docs/roadmap.md RM-36 follow-up). */
  reasoning_content?: string;
  tool_calls?: ToolCallDelta[];
}

export interface StreamUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/**
 * RM-36: same real endpoint as usePlaygroundChat, but with stream: true,
 * parsed as Server-Sent Events and reported incrementally via onDelta — not a
 * react-query mutation, since progressive UI updates don't fit that model.
 * The response never carries a standard OpenAI `usage` field for a streamed
 * request, but llama.cpp's final chunk includes its own `timings` object —
 * `cache_n` (tokens already in the KV cache) + `prompt_n` (newly processed)
 * equals the real total prompt token count, and `predicted_n` is the real
 * completion token count (confirmed against the same request's non-streaming
 * `usage` — cache_n + prompt_n matched usage.prompt_tokens exactly, and
 * predicted_n matched usage.completion_tokens exactly). Real counts, not an
 * estimate — just under a llama.cpp-specific field, not the OpenAI one.
 */
export async function streamPlaygroundChat(
  { model, messages, params }: { model: string; messages: ChatMessage[]; params: PlaygroundParams },
  onDelta: (delta: StreamDelta) => void,
): Promise<{ finishReason: string | null; usage: StreamUsage | null }> {
  const token = getStoredToken();
  const response = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ model, messages, stream: true, ...params }),
  });

  if (response.status === 401) {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore — keep the generic message */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finishReason: string | null = null;
  let usage: StreamUsage | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data:")) {
        // llama.cpp can commit to a 200 streaming response and then write a
        // plain (non-SSE-prefixed) {"error": {...}} line if the request turns
        // out invalid mid-stream (e.g. malformed conversation history) —
        // surface it instead of silently dropping it as an unrecognized line.
        if (line.startsWith("{") && line.includes('"error"')) {
          let message: string | undefined;
          try {
            message = JSON.parse(line)?.error?.message;
          } catch {
            /* not valid JSON after all — fall through and ignore the line */
          }
          if (message) throw new Error(message);
        }
        continue;
      }
      const data = line.slice(5).trim();
      if (data === "[DONE]" || !data) continue;
      let chunk: {
        choices?: { delta?: StreamDelta; finish_reason?: string | null }[];
        timings?: { cache_n?: number; prompt_n?: number; predicted_n?: number };
      };
      try {
        chunk = JSON.parse(data);
      } catch {
        continue;
      }
      if (chunk.timings?.prompt_n !== undefined && chunk.timings.predicted_n !== undefined) {
        const promptTokens = (chunk.timings.cache_n ?? 0) + chunk.timings.prompt_n;
        const completionTokens = chunk.timings.predicted_n;
        usage = {
          prompt_tokens: promptTokens,
          completion_tokens: completionTokens,
          total_tokens: promptTokens + completionTokens,
        };
      }
      const choice = chunk.choices?.[0];
      if (!choice) continue;
      if (choice.finish_reason) finishReason = choice.finish_reason;
      if (choice.delta) onDelta(choice.delta);
    }
  }

  return { finishReason, usage };
}
