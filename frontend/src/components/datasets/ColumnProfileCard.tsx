import React from "react";
import { Calendar, Hash, Type, ToggleLeft, AlertTriangle } from "lucide-react";
import type { DatasetColumnProfile } from "@/types";
import { cn } from "@/lib/utils";
import { CategoryBars, HealthBar, Histogram } from "./Histogram";

const TYPE_META: Record<string, { icon: React.ElementType; label: string; abbr: string }> = {
  string:  { icon: Type,        label: "Text",    abbr: "ab" },
  integer: { icon: Hash,        label: "Integer", abbr: "99" },
  float:   { icon: Hash,        label: "Number",  abbr: "1.2" },
  date:    { icon: Calendar,    label: "Date",    abbr: "📅" },
  boolean: { icon: ToggleLeft,  label: "Boolean", abbr: "T/F" },
};

interface Props {
  column: DatasetColumnProfile;
  /** Full preview rows so we can compute the actual distribution (not just samples). */
  values?: any[];
  /** Width of the card — should match the underlying data grid column. */
  width?: number;
  selected?: boolean;
  onClick?: () => void;
}

export const ColumnProfileCard: React.FC<Props> = ({ column, values, width = 220, selected, onClick }) => {
  const meta = TYPE_META[column.inferred_type || "string"] || TYPE_META.string;
  const Icon = meta.icon;

  // Build the visual distribution
  const numericValues = (values ?? [])
    .map((v) => typeof v === "number" ? v : Number(v))
    .filter((n) => Number.isFinite(n));

  const stringBuckets: Record<string, number> = {};
  for (const v of values ?? []) {
    if (v == null || v === "") continue;
    const s = String(v);
    stringBuckets[s] = (stringBuckets[s] || 0) + 1;
  }
  const buckets = Object.entries(stringBuckets).map(([label, count]) => ({ label, count }));

  // Total count of non-null values (for the right-aligned "n distinct" line)
  const isNumeric = column.inferred_type === "integer" || column.inferred_type === "float";
  const isDate = column.inferred_type === "date";

  return (
    <div
      onClick={onClick}
      style={{ width }}
      className={cn(
        "shrink-0 cursor-pointer border-r border-line bg-white px-3 py-2.5 transition",
        selected ? "bg-brand-subtle" : "hover:bg-canvas",
      )}
    >
      {/* Header row */}
      <div className="flex items-center gap-1.5">
        <Icon className="h-3 w-3 shrink-0 text-ink-muted" />
        <div className="flex-1 truncate text-[11px] font-semibold text-ink" title={column.column_name}>
          {column.column_name}
        </div>
      </div>

      {/* Health bar */}
      <HealthBar nullPercent={column.null_percent ?? 0} className="mt-1.5" />

      {/* Distribution */}
      <div className="mt-2">
        {isNumeric && numericValues.length > 0 ? (
          <Histogram values={numericValues} height={36} />
        ) : isDate && values && values.length > 0 ? (
          <Histogram
            values={(values || []).map((v) => {
              const t = Date.parse(String(v));
              return Number.isFinite(t) ? t : NaN;
            }).filter(Number.isFinite)}
            height={36}
          />
        ) : (
          <CategoryBars buckets={buckets} total={values?.length ?? 0} height={36} />
        )}
      </div>

      {/* Footer stats */}
      <div className="mt-1.5 space-y-0.5 text-[10px] text-ink-muted">
        {column.null_percent != null && column.null_percent > 0 && (
          <div className="flex items-center gap-1 text-warning">
            <AlertTriangle className="h-2.5 w-2.5" />
            <span>{column.null_percent}% missing</span>
          </div>
        )}
        {(isNumeric || isDate) && column.min_value != null && column.max_value != null && (
          <div className="flex justify-between font-mono">
            <span className="truncate">{String(column.min_value)}</span>
            <span className="truncate text-right">{String(column.max_value)}</span>
          </div>
        )}
        {!isNumeric && !isDate && (
          <div className="font-mono">{column.distinct_count} distinct</div>
        )}
      </div>
    </div>
  );
};
