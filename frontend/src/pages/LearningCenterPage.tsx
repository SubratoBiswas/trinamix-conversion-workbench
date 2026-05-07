import React, { useEffect, useState } from "react";
import {
  BookOpen, TrendingUp, Zap, Clock, Download, GraduationCap,
  Sparkles, Trash2, Link2, ChevronRight,
} from "lucide-react";
import { LearningApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate, cn } from "@/lib/utils";
import type { LearnedMapping, LearningStats } from "@/types";

export const LearningCenterPage: React.FC = () => {
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [items, setItems] = useState<LearnedMapping[] | null>(null);

  const refresh = () => {
    LearningApi.stats().then(setStats);
    LearningApi.list().then(setItems);
  };
  useEffect(() => { refresh(); }, []);

  if (!stats || !items) return <PageLoader />;

  const isEmpty = stats.total === 0;

  return (
    <>
      <PageTitle
        title="Learning Center"
        subtitle={isEmpty
          ? "AI feedback loop — analyst actions train the matching engine"
          : `${stats.total} learned mapping(s) — auto-applied in future cycles`
        }
        right={!isEmpty && (
          <Button variant="secondary">
            <Download className="h-4 w-4" /> Export Registry
          </Button>
        )}
      />

      {isEmpty ? (
        <EmptyHero />
      ) : (
        <KpiStrip stats={stats} />
      )}

      <ReferenceStandards
        items={items.filter((m) => m.kind === "reference_standard")}
        onForget={async (id) => { await LearningApi.delete(id); refresh(); }}
      />

      {/* Category cards — always shown so users see the buckets even when empty */}
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stats.by_category.map((c) => (
          <CategoryCard key={c.category} category={c.category} count={c.count} />
        ))}
      </div>

      {/* Registry table */}
      {items.length > 0 && (
        <Card className="mt-5">
          <CardHeader title="Learned Mapping Registry" subtitle={`${items.length} entr${items.length === 1 ? "y" : "ies"}`} />
          <table className="table-shell">
            <thead>
              <tr>
                <th>Mapping ID</th>
                <th>Type</th>
                <th>Original value</th>
                <th>Resolved value</th>
                <th>Object</th>
                <th>Captured from</th>
                <th className="text-right">Confidence boost</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id}>
                  <td className="font-mono text-[11px] text-ink-muted">LM-{String(m.id).padStart(8, "0")}</td>
                  <td><Pill tone="brand">{m.category}</Pill></td>
                  <td className="font-mono text-danger">{m.original_value}</td>
                  <td className="font-mono text-success">{m.resolved_value}</td>
                  <td className="text-ink-muted">{m.target_object || "—"}</td>
                  <td className="text-[11px] text-ink-muted">{m.captured_from || formatDate(m.captured_at)}</td>
                  <td className="text-right font-mono text-success">+{Math.round((m.confidence_boost || 0) * 100)}%</td>
                  <td className="text-right">
                    <button
                      onClick={async () => { await LearningApi.delete(m.id); refresh(); }}
                      className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
                      title="Forget this rule"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
};

// ─────── Reference Standards section ───────
//
// A Reference Standard is a transformation rule taught on a master entity's
// key column (e.g. Item Master's InventoryItemNumber) that auto-prepends to
// every downstream conversion's FK column referencing the same master.
// Captured automatically when an analyst saves a rule on the master's key
// field — the user doesn't have to manage it explicitly.

const ReferenceStandards: React.FC<{
  items: LearnedMapping[];
  onForget: (id: number) => void | Promise<void>;
}> = ({ items, onForget }) => (
  <Card className="mt-5">
    <CardHeader
      title={
        <span className="inline-flex items-center gap-1.5">
          <Link2 className="h-4 w-4 text-brand" /> Reference Standards
        </span>
      }
      subtitle={
        items.length === 0
          ? "Rules taught on a master entity's key column auto-apply to every downstream FK column"
          : `${items.length} active standard${items.length === 1 ? "" : "s"} — auto-prepended on downstream output`
      }
    />
    {items.length === 0 ? (
      <CardBody>
        <div className="flex items-start gap-3 rounded-md border border-dashed border-line bg-canvas px-4 py-3">
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand-subtle text-brand-dark">
            <Link2 className="h-3.5 w-3.5" />
          </div>
          <div className="text-[12px] text-ink-muted">
            <span className="font-semibold text-ink">No standards yet.</span>{" "}
            Save a transformation on a master conversion's key column (e.g. <span className="font-mono text-ink">InventoryItemNumber</span> on Item Master) and it auto-applies on every downstream conversion's matching FK column — Sales Order, BOM, On-Hand, POs, …
          </div>
        </div>
      </CardBody>
    ) : (
      <table className="table-shell">
        <thead>
          <tr>
            <th>Master entity</th>
            <th>Key column</th>
            <th>Transformation</th>
            <th>Captured from</th>
            <th>Applies to</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td className="font-medium text-ink">{s.target_object}</td>
              <td><code className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[11px]">{s.target_field}</code></td>
              <td>
                <Pill tone="brand">{s.rule_type || "—"}</Pill>
                {s.rule_config && Object.keys(s.rule_config as object).length > 0 && (
                  <span className="ml-1.5 font-mono text-[10.5px] text-ink-muted">
                    {summariseConfig(s.rule_config)}
                  </span>
                )}
              </td>
              <td className="text-[11px] text-ink-muted">{s.captured_from || "—"}</td>
              <td className="text-[11px] text-ink-muted">
                <span className="inline-flex items-center gap-1 text-brand-dark">
                  <ChevronRight className="h-3 w-3" />
                  every downstream {s.target_object} reference
                </span>
              </td>
              <td className="text-right">
                <button
                  onClick={() => onForget(s.id)}
                  className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
                  title="Disable this standard"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    )}
  </Card>
);

const summariseConfig = (cfg: any): string => {
  if (!cfg || typeof cfg !== "object") return "";
  const entries = Object.entries(cfg);
  if (entries.length === 0) return "";
  return entries
    .slice(0, 3)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(", ");
};

// ─────── Empty hero card ───────

const EmptyHero: React.FC = () => (
  <div className="rounded-lg border border-line bg-white px-6 py-12 text-center">
    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-brand-subtle text-brand">
      <BookOpen className="h-5 w-5" />
    </div>
    <div className="mt-4 text-base font-semibold text-ink">No learned mappings yet</div>
    <p className="mx-auto mt-2 max-w-lg text-sm text-ink-muted">
      In the <span className="font-semibold text-ink">Mapping Review</span> screen, click <span className="font-semibold text-ink">Approve &amp; Learn</span> on any AI suggestion. Each capture trains the matching engine and is auto-applied in future cycles — across all conversion categories.
    </p>
  </div>
);

// ─────── Top KPI strip ───────

const KpiStrip: React.FC<{ stats: LearningStats }> = ({ stats }) => (
  <div className="rounded-lg border border-brand/20 bg-gradient-to-br from-brand-subtle/50 to-white p-4">
    <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
      <TrendingUp className="h-3.5 w-3.5" /> Feedback Loop Impact
    </div>
    <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
      <KpiTile icon={Sparkles} label="Mappings captured" value={stats.total} tone="text-brand-dark" />
      <KpiTile icon={TrendingUp} label="Avg confidence boost"
        value={`+${Math.round((stats.avg_confidence_boost || 0) * 100)}%`} tone="text-success" />
      <KpiTile icon={Zap} label="Records auto-fixed" value={stats.records_auto_fixed} tone="text-info" />
      <KpiTile icon={Clock} label="Analyst time saved"
        value={`~${stats.analyst_minutes_saved}m`} tone="text-warning" />
    </div>
  </div>
);

const KpiTile: React.FC<{ icon: React.ElementType; label: string; value: React.ReactNode; tone: string }> = ({ icon: Icon, label, value, tone }) => (
  <div className="rounded-md border border-line bg-white px-4 py-3">
    <div className="flex items-center gap-1.5 text-ink-muted">
      <Icon className={cn("h-3.5 w-3.5", tone)} />
      <span className="text-[10.5px] uppercase tracking-wider">{label}</span>
    </div>
    <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
  </div>
);

// ─────── Category card (CHRM-AI-style) ───────

const CategoryCard: React.FC<{ category: string; count: number }> = ({ category, count }) => (
  <div className={cn(
    "rounded-md border bg-white px-4 py-3 transition",
    count > 0 ? "border-brand/30 hover:border-brand hover:shadow-soft" : "border-line"
  )}>
    <div className={cn("flex h-7 w-7 items-center justify-center rounded-md",
      count > 0 ? "bg-brand-subtle text-brand-dark" : "bg-canvas text-ink-subtle")}>
      <Sparkles className="h-3.5 w-3.5" />
    </div>
    <div className="mt-2 text-sm font-semibold text-ink">{category}</div>
    <div className="mt-0.5 text-[11px] text-ink-muted">{count} mapping{count === 1 ? "" : "s"} captured</div>
  </div>
);
