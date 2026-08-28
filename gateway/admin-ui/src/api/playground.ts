import { useMutation } from "@tanstack/react-query";
import { rootClient } from "./client";

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface PlaygroundParams {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string[];
}

interface ChatCompletionResponse {
  choices: { message: { role: string; content: string }; finish_reason: string | null }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

/**
 * RM-14: calls the gateway's real POST /v1/chat/completions with the admin
 * dashboard's own session token — the same endpoint real clients use, so
 * usage/cost recording and metrics all apply exactly as they would for any
 * other caller. Non-streaming only for this first version.
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
