export type HistoryPolicy = {
  retention_days: number;
  disk_limit_mb: number;
  log_retention_days: number;
  log_disk_limit_mb: number;
  logs?: { used_bytes: number; over_limit: boolean };
  revision: string;
  sources: Record<string, { used_bytes: number; over_limit: boolean }>;
};

const fields = ["retention_days", "disk_limit_mb", "log_retention_days", "log_disk_limit_mb"] as const;

/** Refresh a clean form; preserve explicit edits and their original revision. */
export function refreshedHistoryDraft(saved: HistoryPolicy | undefined, draft: HistoryPolicy | undefined, next: HistoryPolicy) {
  return saved && draft && fields.some(key => saved[key] !== draft[key]) ? draft : next;
}
