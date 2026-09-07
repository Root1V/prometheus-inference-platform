import { cn } from "../lib/cn";

const STATUS_STYLES = {
  active: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  inactive: "bg-gray-100 text-gray-700 dark:bg-gray-500/15 dark:text-gray-300",
} as const;

export function UserStatusBadge({ isActive }: { isActive: boolean }) {
  const status = isActive ? "active" : "inactive";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        STATUS_STYLES[status],
      )}
    >
      {status}
    </span>
  );
}
