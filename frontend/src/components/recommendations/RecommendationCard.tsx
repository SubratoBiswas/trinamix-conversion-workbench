import React from "react";
import {
  Calendar, Hash, Replace, Trash2, Filter as FilterIcon,
  ScissorsLineDashed, Sparkles, Layers, AlertTriangle,
  Plus, X, GraduationCap, CornerDownRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Recommendation, RecommendationKind } from "@/lib/recommendations";

const KIND_META: Record<RecommendationKind, { icon: React.ElementType; tone: string }> = {
  convert_to_date:     { icon: Calendar,             tone: "text-info" },
  convert_to_number:   { icon: Hash,                 tone: "text-info" },
  remove_hyphen:       { icon: ScissorsLineDashed,   tone: "text-success" },
  remove_special_chars:{ icon: Trash2,               tone: "text-success" },
  trim:                { icon: ScissorsLineDashed,   tone: "text-success" },
  uppercase:           { icon: Replace,              tone: "text-success" },
  value_map:           { icon: Replace,              tone: "text-warning" },
  default_value:       { icon: Plus,                 tone: "text-warning" },
  deduplicate:         { icon: Layers,               tone: "text-danger" },
  extract_part:        { icon: FilterIcon,           tone: "text-info" },
  fix_date_format:     { icon: Calendar,             tone: "text-info" },
  fill_missing:        { icon: AlertTriangle,        tone: "text-warning" },
  standardize_uom:     { icon: Replace,              tone: "text-success" },
  length_overflow:     { icon: AlertTriangle,        tone: "text-danger" },
};

interface Props {
  rec: Recommendation;
  onApply?: (rec: Recommendation, learn: boolean) => void;
  onDismiss?: (rec: Recommendation) => void;
  onAddRule?: (rec: Recommendation) => void;
  applied?: boolean;
  learned?: boolean;
}

export const RecommendationCard: React.FC<Props> = ({ rec, onApply, onDismiss, onAddRule, applied, learned }) => {
  const meta = KIND_META[rec.kind] || KIND_META.trim;
  const Icon = meta.icon;
  const conf = Math.round(rec.confidence * 100);

  return (
    <div className={cn(
      "group rounded-md border bg-white px-3 py-2.5 shadow-sm transition",
      applied ? "border-success/40 bg-success-subtle/30" :
      learned ? "border-brand/40 bg-brand-subtle/40" :
      "border-line hover:border-brand/40 hover:shadow-soft",
    )}>
      <div className="flex items-start gap-2">
        <div className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas", meta.tone)}>
          <Icon className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <div className="flex-1 truncate text-[12.5px] font-semibold text-ink">{rec.title}</div>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-muted">{conf}%</span>
          </div>
          <div className="mt-0.5 text-[11.5px] leading-snug text-ink-muted">{rec.reason}</div>

          {/* Preview before/after */}
          {rec.preview && rec.preview.length > 0 && (
            <div className="mt-2 space-y-1 rounded border border-line bg-canvas px-2 py-1.5">
              {rec.preview.slice(0, 2).map((p, i) => (
                <div key={i} className="flex items-center gap-1.5 font-mono text-[10.5px]">
                  <span className="truncate text-danger line-through">{p.before}</span>
                  <CornerDownRight className="h-3 w-3 shrink-0 text-ink-subtle" />
                  <span className="truncate text-success">{p.after}</span>
                </div>
              ))}
            </div>
          )}

          {/* Footer meta */}
          <div className="mt-2 flex items-center justify-between text-[10.5px] text-ink-muted">
            <span>
              <span className="font-medium text-ink">{rec.impact.records.toLocaleString()}</span> record{rec.impact.records === 1 ? "" : "s"}
              {rec.targetField && (
                <span className="ml-1.5">→ <span className="font-medium text-ink">{rec.targetField}</span></span>
              )}
            </span>
            <span className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[10px] text-ink-subtle">{rec.column}</span>
          </div>
        </div>
      </div>

      {/* Actions */}
      {!applied && !learned && (
        <div className="mt-2 flex items-center gap-1 border-t border-line pt-2">
          <button
            onClick={() => onApply?.(rec, false)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-brand-dark hover:bg-brand-subtle"
            title="Apply this recommendation now"
          >
            <Sparkles className="h-3 w-3" /> Apply
          </button>
          <button
            onClick={() => onApply?.(rec, true)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-brand-dark hover:bg-brand-subtle"
            title="Apply and capture as a learned rule for future cycles"
          >
            <GraduationCap className="h-3 w-3" /> Apply &amp; Learn
          </button>
          <div className="flex-1" />
          {onAddRule && (
            <button
              onClick={() => onAddRule?.(rec)}
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-ink-muted hover:bg-canvas hover:text-ink"
              title="Open in Transformation Studio"
            >
              <Plus className="h-3 w-3" /> Rule
            </button>
          )}
          <button
            onClick={() => onDismiss?.(rec)}
            className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-ink"
            title="Dismiss"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {(applied || learned) && (
        <div className="mt-2 flex items-center gap-1 border-t border-line pt-2 text-[11px] text-ink-muted">
          {applied && <span className="inline-flex items-center gap-1 text-success"><Sparkles className="h-3 w-3" /> Applied</span>}
          {learned && <span className="inline-flex items-center gap-1 text-brand-dark"><GraduationCap className="h-3 w-3" /> Learned</span>}
        </div>
      )}
    </div>
  );
};
