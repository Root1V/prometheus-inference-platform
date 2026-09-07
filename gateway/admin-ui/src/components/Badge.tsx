import type { ReactNode } from "react";
import { cn } from "../lib/cn";

const TONE_STYLES = {
  neutral: "bg-gray-100 text-gray-700 dark:bg-gray-500/15 dark:text-gray-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  purple: "bg-purple-100 text-purple-700 dark:bg-purple-500/15 dark:text-purple-400",
  amber: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  green: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
} as const;

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: keyof typeof TONE_STYLES;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        TONE_STYLES[tone],
      )}
    >
      {children}
    </span>
  );
}

const MODALITY_TONE: Record<string, keyof typeof TONE_STYLES> = {
  text: "blue",
  vision: "purple",
  embedding: "amber",
  image: "green",
};

export function ModalityBadge({ modality }: { modality: string }) {
  return <Badge tone={MODALITY_TONE[modality] ?? "neutral"}>{modality}</Badge>;
}
