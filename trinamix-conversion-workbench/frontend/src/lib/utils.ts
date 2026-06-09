import clsx, { type ClassValue } from "clsx";

export const cn = (...args: ClassValue[]) => clsx(args);

export const formatDate = (iso?: string | null) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
};

export const formatNumber = (n: number) =>
  n.toLocaleString(undefined, { maximumFractionDigits: 2 });

export const truncate = (s: string | null | undefined, n = 60) => {
  if (!s) return "";
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
};

export const statusTone = (status: string): "success" | "warning" | "danger" | "info" | "neutral" => {
  const s = status.toLowerCase();
  if (["loaded", "validated", "approved", "success", "completed", "passed", "ok"].includes(s)) return "success";
  if (["failed", "error", "critical", "rejected"].includes(s)) return "danger";
  if (["running", "warning", "awaiting_approval", "mapping_suggested"].includes(s)) return "warning";
  if (["draft", "suggested", "info", "saved"].includes(s)) return "info";
  return "neutral";
};

export const severityTone = (sev: string): "success" | "warning" | "danger" | "info" => {
  const s = sev.toLowerCase();
  if (s === "critical" || s === "error") return "danger";
  if (s === "warning") return "warning";
  if (s === "info") return "info";
  return "success";
};

// Confidence color band — used purposefully on confidence bars only
export const confidenceTone = (c: number): "success" | "warning" | "danger" => {
  if (c >= 0.7) return "success";
  if (c >= 0.4) return "warning";
  return "danger";
};
