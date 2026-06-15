import React, { useEffect, useState } from "react";
import {
  BookOpen, TrendingUp, Zap, Clock, Download, GraduationCap,
  Sparkles, Trash2, Link2, ChevronRight, Database, Brain, Repeat,
} from "lucide-react";
import { LearningApi, ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate, cn } from "@/lib/utils";
import type { KnowledgeBankStat, LearnedMapping, LearningStats, Project } from "@/types";

// Server-driven enum has the canonical display name. This static fallback
// avoids a second async load on the Learning Center for a tiny dictionary.
const SOURCE_DISPLAY: Record<string, string> = {
  netsuite: "NetSuite",
  oracle_ebs: "Oracle EBS",
  sap_ecc: "SAP ECC",
  sap_s4: "SAP S/4 HANA",
  workday: "Workday",
  jde: "JD Edwards",
  custom: "Custom",
};

export const LearningCenterPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  // null = "All engagements" (cross-project rollup); number = filter to
  // one engagement's contributions to the bank. The Knowledge Bank
  // rollup card is always cross-project — that's its whole point.
  const [projectId, setProjectId] = useState<number | null>(null);
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [items, setItems] = useState<LearnedMapping[] | null>(null);
  const [kbStats, setKbStats] = useState<KnowledgeBankStat[] | null>(null);

  const refresh = () => {
    const filter = projectId ? { project_id: projectId } : undefined;
    LearningApi.stats(filter).then(setStats);
    LearningApi.list(filter).then(setItems);
    LearningApi.knowledgeBankStats()
      .then(setKbStats)
      .catch(() => setKbStats([]));
  };
  useEffect(() => {
    ProjectsApi.list().then(setProjects);
  }, []);
  useEffect(() => { refresh(); }, [projectId]);

  if (!stats || !items) return <PageLoader />;

  const isEmpty = stats.total === 0;
  const project = projects.find((p) => p.id === projectId);

  return (
    <>
      <PageTitle
        title="Learning Center"
        subtitle={isEmpty
          ? "AI feedback loop — analyst actions train the matching engine"
          : `${stats.total} learned mapping(s) — auto-applied in future cycles${project ? ` · scoped to ${project.name}` : ""}`
        }
        right={
          <div className="flex items-center gap-2">
            <select
              className="input !h-9 !text-sm min-w-[220px]"
              value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value === "" ? null : Number(e.target.value))}
              title="Filter by engagement"
            >
              <option value="">All engagements</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}{p.client ? ` · ${p.client}` : ""}</option>
              ))}
            </select>
            {!isEmpty && (
              <Button variant="secondary">
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

      <KnowledgeBankRollup stats={kbStats} />

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

// ─────── Knowledge Bank rollup ───────
//
// Cross-source-system view: every prior approved mapping bucketed by the
// source ERP it was taught against. Drives the "new EBS project benefits
// from prior EBS projects on day 1" story. Source-isolated so a NetSuite
// alias never accidentally pre-populates an EBS conversion.

const KnowledgeBankRollup: React.FC<{ stats: KnowledgeBankStat[] | null }> = ({
  stats,
}) => (
  <Card className="mt-5">
    <CardHeader
      title={
        <span className="inline-flex items-center gap-1.5">
          <Brain className="h-4 w-4 text-brand" /> Mapping Knowledge Bank
        </span>
      }
      subtitle={
        stats && stats.length > 0
          ? `${stats.length} source system${stats.length === 1 ? "" : "s"} learned — pre-applied on every new project of the same source`
          : "Captures every approved mapping per source ERP. The first project starts empty; every subsequent project benefits."
      }
    />
    {!stats ? (
      <CardBody>
        <div className="text-xs text-ink-muted">Loading Knowledge Bank…</div>
      </CardBody>
    ) : stats.length === 0 ? (
      <CardBody>
        <div className="flex items-start gap-3 rounded-md border border-dashed border-line bg-canvas px-4 py-3">
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand-subtle text-brand-dark">
            <Database className="h-3.5 w-3.5" />
          </div>
          <div className="text-[12px] text-ink-muted">
            <span className="font-semibold text-ink">No source-system mappings captured yet.</span>{" "}
            Approve a mapping in any project — the Knowledge Bank stores it
            against that project's source (NetSuite, Oracle EBS, …). Every
            subsequent project on the same source picks up these mappings as
            pre-fills at 0.85 confidence, status "suggested" (analyst confirms).
          </div>
        </div>
      </CardBody>
    ) : (
      <div className="grid grid-cols-1 gap-3 px-5 py-4 md:grid-cols-2 lg:grid-cols-3">
        {stats.map((s) => (
          <div
            key={s.source_system}
            className="rounded-lg border border-line bg-white px-4 py-3 transition hover:border-brand/40 hover:shadow-soft"
          >
            <div className="flex items-center justify-between">
              <div className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
                <Database className="h-3.5 w-3.5 text-brand-dark" />
                {SOURCE_DISPLAY[s.source_system] || s.source_system}
              </div>
              <Pill tone="brand" className="!text-[9px]">KB</Pill>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-1.5 text-center text-[10.5px]">
              <BankTile label="Mappings" value={s.mappings} />
              <BankTile label="Rules"    value={s.rules} />
              <BankTile label="Ref std." value={s.reference_standards} />
            </div>
            <div className="mt-2.5 flex items-center justify-between border-t border-line/60 pt-2 text-[10.5px] text-ink-muted">
              <span className="inline-flex items-center gap-1">
                <Repeat className="h-3 w-3" />
                {s.total_reuses} reuse{s.total_reuses === 1 ? "" : "s"}
                {s.avg_reuse_per_mapping > 0 && (
                  <span className="ml-1 font-mono">
                    (avg {s.avg_reuse_per_mapping}/mapping)
                  </span>
                )}
              </span>
              <span>
                {s.project_count} project{s.project_count === 1 ? "" : "s"}
              </span>
            </div>
            {s.last_reused_at && (
              <div className="mt-1 text-[10px] text-ink-muted">
                last reused {formatDate(s.last_reused_at)}
              </div>
            )}
          </div>
        ))}
      </div>
    )}
  </Card>
);

const BankTile: React.FC<{ label: string; value: number }> = ({ label, value }) => (
  <div className="rounded-md bg-canvas px-1.5 py-1.5">
    <div className="font-mono text-base font-semibold tabular-nums text-ink">{value}</div>
    <div className="text-[9.5px] uppercase tracking-wider text-ink-muted">{label}</div>
  </div>
);

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
