import { useMutation } from "@tanstack/react-query";
import { rootClient } from "./client";

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
    mutationFn: async ({ model, prompt }: { model: string; prompt: string }) =>
      (
        await rootClient.post<ChatCompletionResponse>("/v1/chat/completions", {
          model,
          messages: [{ role: "user", content: prompt }],
          stream: false,
        })
      ).data,
  });
}
