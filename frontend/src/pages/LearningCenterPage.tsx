import React, { useEffect, useState } from "react";
import {
  BookOpen, TrendingUp, Zap, Clock, Download, Sparkles, Trash2, Link2, ChevronRight,
} from "lucide-react";
import { LearningApi, ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate, cn } from "@/lib/utils";
import type { LearnedMapping, LearningStats, Project } from "@/types";

export const LearningCenterPage: React.FC = () => {
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [items, setItems] = useState<LearnedMapping[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [backfilling, setBackfilling] = useState(false);

  const refresh = (pid?: string) => {
    const params = pid ? { project_id: pid } : undefined;
    LearningApi.stats(params).then(setStats);
    LearningApi.list(params ? { ...params } : undefined).then(setItems);
  };

  useEffect(() => {
    refresh();
    ProjectsApi.list().then(setProjects).catch(() => {});
  }, []);

  const handleProjectChange = (pid: string) => {
    setSelectedProjectId(pid);
    setStats(null);
    setItems(null);
    refresh(pid || undefined);
  };

  if (!stats || !items) return <PageLoader />;

  const isEmpty = stats.total === 0;

  return (
    <>
      <PageTitle
        title="Learning Center"
        subtitle={isEmpty
          ? "AI feedback loop — analyst actions train the matching engine"
          : `${stats.total} learned mapping(s)${selectedProjectId ? " in this engagement" : ""} — auto-applied in future cycles`
        }
        right={
          <div className="flex items-center gap-2">
            {projects.length > 0 && (
              <select
                value={selectedProjectId}
                onChange={(e) => handleProjectChange(e.target.value)}
                className="h-9 rounded-md border border-line bg-white pl-3 pr-8 text-sm text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              >
                <option value="">All engagements</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.client ? ` · ${p.client}` : ""}
                  </option>
                ))}
              </select>
            )}
            {!isEmpty && (
              <Button variant="secondary" onClick={async () => {
                setBackfilling(true);
                try {
                  const result = await LearningApi.backfillProjects();
                  alert(`Backfill complete: ${result.updated} updated, ${result.skipped_no_match} skipped`);
                  refresh(selectedProjectId || undefined);
                } finally {
                  setBackfilling(false);
                }
              }} disabled={backfilling}>
                {backfilling ? "Fixing..." : "Fix Project Links"}
              </Button>
            )}
            {!isEmpty && (
              <Button variant="secondary" onClick={() => {
                const rows = items.map((m) =>
                  [m.id, m.kind, m.category,
                   JSON.stringify(m.original_value ?? ""),
                   JSON.stringify(m.resolved_value ?? ""),
                   m.target_object || "",
                   m.captured_at].join(",")
                );
                const csv = ["id,kind,category,original_value,resolved_value,target_object,captured_at", ...rows].join("\n");
                const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
                const a = document.createElement("a"); a.href = url; a.download = "learned_mappings.csv";
                document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
              }}>
                <Download className="h-4 w-4" /> Export Registry
              </Button>
            )}
          </div>
        }
      />

      {isEmpty ? (
        <EmptyHero />
      ) : (
        <KpiStrip stats={stats} />
      )}

      <ReferenceStandards
        items={items.filter((m) => m.kind === "reference_standard")}
        onForget={async (id) => { await LearningApi.delete(id); refresh(selectedProjectId || undefined); }}
      />

      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stats.by_category.map((c) => (
          <CategoryCard key={c.category} category={c.category} count={c.count} />
        ))}
      </div>

      {items.length > 0 && (
        <Card className="mt-5">
          <CardHeader
            title="Learned Mapping Registry"
            subtitle={`${items.length} entr${items.length === 1 ? "y" : "ies"}${selectedProjectId ? " in this engagement" : ""}`}
          />
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
                  <td className="font-mono text-[11px] text-ink-muted">LM-{String(m.id).slice(-8).toUpperCase()}</td>
                  <td><Pill tone="brand">{m.category}</Pill></td>
                  <td className="font-mono text-danger">{m.original_value}</td>
                  <td className="font-mono text-success">{m.resolved_value}</td>
                  <td className="text-ink-muted">{m.target_object || "—"}</td>
                  <td className="text-[11px] text-ink-muted">{m.captured_from || formatDate(m.captured_at)}</td>
                  <td className="text-right font-mono text-success">+{Math.round((m.confidence_boost || 0) * 100)}%</td>
                  <td className="text-right">
                    <button
                      onClick={async () => { await LearningApi.delete(m.id); refresh(selectedProjectId || undefined); }}
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

      {items.length === 0 && !isEmpty && selectedProjectId && (
        <div className="mt-5 rounded-lg border border-line bg-white px-6 py-10 text-center text-sm text-ink-muted">
          No learned mappings captured for this engagement yet.
          <br />
          <span className="text-xs">Try clicking "Fix Project Links" to backfill existing mappings.</span>
        </div>
      )}
    </>
  );
};

// -- Reference Standards section --

const ReferenceStandards: React.FC<{
  items: LearnedMapping[];
  onForget: (id: string) => void | Promise<void>;
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
            Save a transformation on a master conversion's key column (e.g. <span className="font-mono text-ink">InventoryItemNumber</span> on Item Master) and it auto-applies on every downstream conversion's matching FK column.
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

// -- Empty hero --

const EmptyHero: React.FC = () => (
  <div className="rounded-lg border border-line bg-white px-6 py-12 text-center">
    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-brand-subtle text-brand">
      <BookOpen className="h-5 w-5" />
    </div>
    <div className="mt-4 text-base font-semibold text-ink">No learned mappings yet</div>
    <p className="mx-auto mt-2 max-w-lg text-sm text-ink-muted">
      In the <span className="font-semibold text-ink">Mapping Review</span> screen, click <span className="font-semibold text-ink">Approve &amp; Learn</span> on any AI suggestion.
    </p>
  </div>
);

// -- KPI strip --

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

// -- Category card --

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
