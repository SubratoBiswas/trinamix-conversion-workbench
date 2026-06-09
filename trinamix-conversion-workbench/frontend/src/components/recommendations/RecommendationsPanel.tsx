import React, { useMemo, useState } from "react";
import { Sparkles, Filter as FilterIcon, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { CATEGORY_LABELS, type Recommendation, type RecommendationCategory } from "@/lib/recommendations";
import { RecommendationCard } from "./RecommendationCard";

interface Props {
  recommendations: Recommendation[];
  appliedIds?: Set<string>;
  learnedIds?: Set<string>;
  /** Optionally filter by a specific column (e.g. clicking a profile card). */
  columnFilter?: string | null;
  setColumnFilter?: (col: string | null) => void;
  onApply?: (rec: Recommendation, learn: boolean) => void;
  onDismiss?: (rec: Recommendation) => void;
  onAddRule?: (rec: Recommendation) => void;
  onRefresh?: () => void;
  loading?: boolean;
  className?: string;
}

const FILTERS: { value: "all" | RecommendationCategory; label: string }[] = [
  { value: "all",                label: "All" },
  { value: "required",           label: "Required" },
  { value: "data_type",          label: "Data Type" },
  { value: "formatting",         label: "Formatting" },
  { value: "value_translation",  label: "Translation" },
  { value: "deduplication",      label: "Dedup" },
];

export const RecommendationsPanel: React.FC<Props> = ({
  recommendations, appliedIds = new Set(), learnedIds = new Set(),
  columnFilter, setColumnFilter, onApply, onDismiss, onAddRule, onRefresh,
  loading, className,
}) => {
  const [filter, setFilter] = useState<"all" | RecommendationCategory>("all");

  const visible = useMemo(() => {
    return recommendations.filter((r) => {
      if (filter !== "all" && r.category !== filter) return false;
      if (columnFilter && r.column !== columnFilter) return false;
      return true;
    });
  }, [recommendations, filter, columnFilter]);

  return (
    <aside className={cn("flex h-full flex-col border-l border-line bg-canvas", className)}>
      {/* Header */}
      <div className="border-b border-line bg-white px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-subtle text-brand">
              <Sparkles className="h-3.5 w-3.5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-ink">Recommendations</div>
              <div className="text-[11px] text-ink-muted">{visible.length} of {recommendations.length}</div>
            </div>
          </div>
          {onRefresh && (
            <button onClick={onRefresh} className="rounded p-1.5 text-ink-muted hover:bg-canvas hover:text-ink" title="Recompute">
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            </button>
          )}
        </div>

        {/* Category filters */}
        <div className="mt-3 flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[10.5px] font-medium transition",
                filter === f.value
                  ? "bg-brand text-white"
                  : "bg-canvas text-ink-muted hover:bg-line hover:text-ink"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        {columnFilter && (
          <div className="mt-2 flex items-center gap-1.5 rounded-md bg-brand-subtle px-2 py-1 text-[11px] text-brand-dark">
            <FilterIcon className="h-3 w-3" />
            <span className="flex-1 truncate font-mono">{columnFilter}</span>
            <button onClick={() => setColumnFilter?.(null)} className="text-brand-dark hover:underline">clear</button>
          </div>
        )}
      </div>

      {/* List */}
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {visible.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-4 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-success-subtle text-success">
              <Sparkles className="h-5 w-5" />
            </div>
            <div className="mt-3 text-sm font-medium text-ink">No recommendations</div>
            <div className="mt-1 text-xs text-ink-muted">
              {recommendations.length === 0
                ? "Profile a dataset to surface AI suggestions."
                : "All visible recommendations have been handled."}
            </div>
          </div>
        ) : (
          visible.map((rec) => (
            <RecommendationCard
              key={rec.id}
              rec={rec}
              applied={appliedIds.has(rec.id)}
              learned={learnedIds.has(rec.id)}
              onApply={onApply}
              onDismiss={onDismiss}
              onAddRule={onAddRule}
            />
          ))
        )}
      </div>
    </aside>
  );
};
