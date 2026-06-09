import React from "react";
import { cn } from "@/lib/utils";

/**
 * Compact histogram shown in column profile cards. Inline SVG, no external deps.
 * Bars use neutral indigo by default; pass `tone="warning"` to flag null-heavy
 * columns visually.
 */
export const Histogram: React.FC<{
  values: number[];
  bins?: number;
  height?: number;
  className?: string;
  tone?: "brand" | "muted";
}> = ({ values, bins = 16, height = 40, className, tone = "brand" }) => {
  if (!values || values.length === 0) {
    return <div className={cn("flex items-center justify-center text-[10px] text-ink-subtle", className)} style={{ height }}>no values</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = range / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.floor((v - min) / step));
    counts[idx]++;
  }
  const peak = Math.max(...counts) || 1;
  const fill = tone === "brand" ? "#A5B4FC" : "#CBD5E1";
  return (
    <svg viewBox={`0 0 ${bins * 4} ${height}`} preserveAspectRatio="none" className={cn("w-full", className)} style={{ height }}>
      {counts.map((c, i) => {
        const h = Math.max(1.5, (c / peak) * (height - 4));
        return <rect key={i} x={i * 4 + 0.5} y={height - h} width={3} height={h} fill={fill} rx={0.5} />;
      })}
    </svg>
  );
};

/**
 * Categorical distribution — top values as horizontal bars + remainder bucket.
 * Used for string columns where a histogram doesn't make sense.
 */
export const CategoryBars: React.FC<{
  buckets: { label: string; count: number }[];
  total: number;
  height?: number;
  className?: string;
}> = ({ buckets, total, height = 40, className }) => {
  if (!buckets || buckets.length === 0 || total === 0) {
    return <div className={cn("flex items-center justify-center text-[10px] text-ink-subtle", className)} style={{ height }}>no values</div>;
  }
  const sorted = [...buckets].sort((a, b) => b.count - a.count).slice(0, 4);
  const peak = Math.max(...sorted.map((b) => b.count)) || 1;
  return (
    <div className={cn("flex flex-col gap-1 overflow-hidden", className)} style={{ height }}>
      {sorted.map((b, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-brand-light" style={{ width: `${(b.count / peak) * 100}%` }} />
          </div>
          <div className="w-12 truncate text-[10px] font-mono text-ink-muted" title={b.label}>{b.label || "—"}</div>
        </div>
      ))}
    </div>
  );
};

/**
 * Single horizontal "health" bar shown at the top of every profile card.
 * Encodes null-rate visually so the eye scans across columns instantly.
 *  - 0–5% null    → green (clean)
 *  - 5–25% null   → amber (warning)
 *  - 25%+ null    → red (issue)
 */
export const HealthBar: React.FC<{ nullPercent: number; className?: string }> = ({ nullPercent, className }) => {
  const tone = nullPercent < 5 ? "bg-success" : nullPercent < 25 ? "bg-warning" : "bg-danger";
  // Show null portion in the warning/danger tone, valid portion in green
  const valid = Math.max(0, 100 - nullPercent);
  return (
    <div className={cn("flex h-1 w-full overflow-hidden rounded-full bg-line", className)}>
      <div className="bg-success" style={{ width: `${valid}%` }} />
      <div className={tone} style={{ width: `${nullPercent}%` }} />
    </div>
  );
};
