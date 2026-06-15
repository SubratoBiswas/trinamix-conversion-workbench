import React, { useEffect, useMemo, useState } from "react";
import {
  Sparkles, Database, Cog, Workflow, Zap, FileBarChart, Cable,
  RefreshCw, AlertTriangle, CheckCircle2, ChevronRight, X,
  ShieldCheck, Loader2, Link2, Activity,
} from "lucide-react";
import { DiscoveryApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type { DiscoveryLatest, DiscoveredObject } from "@/types";

/**
 * Discovery section — embedded inside Project Overview (no new top-level
 * route). Renders the six-pillar inventory grid + Integration Health
 * table for the project's most-recent completed scan. Each pillar card
 * opens a drilldown modal with the raw discovered objects + per-category
 * counts. The Re-scan button kicks off a fresh inventory run.
 */

interface PillarSpec {
  code: "data" | "configuration" | "processes" | "customisations" | "reports" | "integrations";
  label: string;
  icon: React.ElementType;
  accent: string;     // border colour on the pillar card
  subtitle: string;
}

const PILLARS: PillarSpec[] = [
  { code: "data",           label: "Data",           icon: Database,    accent: "border-info/60",    subtitle: "records × entity types" },
  { code: "configuration",  label: "Configuration",  icon: Cog,         accent: "border-brand/60",   subtitle: "setup objects" },
  { code: "processes",      label: "Processes",      icon: Workflow,    accent: "border-warning/60", subtitle: "workflows & approvals" },
  { code: "customisations", label: "Customisations", icon: Zap,         accent: "border-danger/60",  subtitle: "scripts & custom fields" },
  { code: "reports",        label: "Reports",        icon: FileBarChart,accent: "border-success/60", subtitle: "saved searches & reports" },
  { code: "integrations",   label: "Integrations",   icon: Cable,       accent: "border-info/60",    subtitle: "interfaces & APIs" },
];

const HEALTH_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  healthy: "success",
  degraded: "warning",
  not_tested: "neutral",
  failed: "danger",
};

const HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  not_tested: "Not tested",
  failed: "Failed",
};

export const DiscoveryPanel: React.FC<{
  projectId: number;
  hasConnection: boolean;
}> = ({ projectId, hasConnection }) => {
  const [latest, setLatest] = useState<DiscoveryLatest | null | undefined>(undefined);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<PillarSpec["code"] | null>(null);

  const reload = async () => {
    try {
      const res = await DiscoveryApi.latest(projectId);
      setLatest(res);
    } catch {
      setLatest({ run: null, integrations: [] });
    }
  };

  useEffect(() => { reload(); }, [projectId]);

  const runScan = async () => {
    setRunning(true);
    setError(null);
    try {
      await DiscoveryApi.run(projectId);
      await reload();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Discovery scan failed");
    } finally {
      setRunning(false);
    }
  };

  if (latest === undefined || latest === null) {
    return (
      <Card className="mt-4">
        <CardHeader title="Discovery" subtitle="Loading…" />
        <CardBody>
          <Loader2 className="h-4 w-4 animate-spin text-ink-muted" />
        </CardBody>
      </Card>
    );
  }

  if (!latest.run) {
    return (
      <Card className="mt-4">
        <CardHeader
          title={<span className="inline-flex items-center gap-1.5"><Sparkles className="h-4 w-4 text-brand" /> Discovery</span>}
          subtitle="Source-system inventory — customisations, integrations, processes, master data."
        />
        <CardBody>
          {hasConnection ? (
            <EmptyState
              icon={<Activity className="h-5 w-5" />}
              title="No discovery scan yet"
              description="Run a discovery scan against this project's source connection to inventory every customisation, integration, process, and master-data entity. Mock-mode scans return instantly with deterministic fixtures."
              action={
                <Button onClick={runScan} loading={running} variant="primary">
                  <Sparkles className="h-4 w-4" /> Run discovery scan
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<AlertTriangle className="h-5 w-5" />}
              title="No source connection yet"
              description="Add a source connection above to enable Discovery. Mock-mode connections work end-to-end."
            />
          )}
          {error && (
            <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
              <AlertTriangle className="mr-1 inline h-3 w-3" /> {error}
            </div>
          )}
        </CardBody>
      </Card>
    );
  }

  const run = latest.run;
  const totalIntegrations = (latest.integrations || []).length;

  return (
    <>
      <Card className="mt-4">
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <Sparkles className="h-4 w-4 text-brand" />
              Discovery
            </span>
          }
          subtitle={
            <span className="text-xs text-ink-muted">
              {run.total_objects.toLocaleString()} objects across 6 pillars · complexity{" "}
              <span className="font-semibold text-ink">{Math.round(run.complexity_score)}</span>/100 · last scan{" "}
              {run.completed_at ? formatDate(run.completed_at) : "—"}
            </span>
          }
          actions={
            <Button onClick={runScan} loading={running} variant="secondary" className="!h-8 !text-xs">
              <RefreshCw className="h-3.5 w-3.5" /> Re-scan
            </Button>
          }
        />
        <CardBody>
          {/* 6 pillar cards */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            {PILLARS.map((p) => (
              <PillarTile
                key={p.code}
                spec={p}
                count={run.pillar_counts[p.code] ?? 0}
                onClick={() => setDrilldown(p.code)}
              />
            ))}
          </div>

          {/* Integration Health table — embedded directly */}
          <IntegrationHealthTable
            integrations={latest.integrations || []}
            healthCounts={run.integration_health || {}}
            totalIntegrations={totalIntegrations}
          />

          {run.scan_notes && (
            <div className="mt-4 inline-flex items-start gap-2 rounded-md bg-canvas px-3 py-2 text-[11px] text-ink-muted">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" />
              {run.scan_notes}
            </div>
          )}
          {error && (
            <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
              <AlertTriangle className="mr-1 inline h-3 w-3" /> {error}
            </div>
          )}
        </CardBody>
      </Card>

      {drilldown && run && (
        <DrilldownModal
          runId={run.id}
          pillar={drilldown}
          spec={PILLARS.find((p) => p.code === drilldown)!}
          onClose={() => setDrilldown(null)}
        />
      )}
    </>
  );
};

const PillarTile: React.FC<{
  spec: PillarSpec;
  count: number;
  onClick: () => void;
}> = ({ spec, count, onClick }) => {
  const Icon = spec.icon;
  return (
    <button
      onClick={onClick}
      className={cn(
        "group flex flex-col items-start gap-1 rounded-lg border-2 bg-white px-3 py-3 text-left transition hover:shadow-soft",
        spec.accent,
      )}
    >
      <div className="flex w-full items-center justify-between">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-canvas">
          <Icon className="h-3.5 w-3.5 text-ink" />
        </div>
        <ChevronRight className="h-3 w-3 text-ink-muted transition group-hover:translate-x-0.5" />
      </div>
      <div className="mt-1 font-mono text-xl font-semibold tabular-nums text-ink">
        {count.toLocaleString()}
      </div>
      <div className="text-[11.5px] font-semibold text-ink">{spec.label}</div>
      <div className="text-[10.5px] text-ink-muted">{spec.subtitle}</div>
    </button>
  );
};

const IntegrationHealthTable: React.FC<{
  integrations: DiscoveredObject[];
  healthCounts: Record<string, number>;
  totalIntegrations: number;
}> = ({ integrations: initial, healthCounts: initialCounts, totalIntegrations }) => {
  // Local copy so re-probes can mutate in-place without forcing a full
  // /latest reload.
  const [rows, setRows] = useState(initial);
  const [counts, setCounts] = useState(initialCounts);
  const [probing, setProbing] = useState<number | null>(null);

  useEffect(() => { setRows(initial); setCounts(initialCounts); }, [initial, initialCounts]);

  if (rows.length === 0) return null;

  const reprobe = async (row: DiscoveredObject) => {
    setProbing(row.id);
    try {
      const refreshed = await DiscoveryApi.reprobe(row.id);
      // Swap the row in place + re-roll the counts.
      const next = rows.map((r) => (r.id === row.id ? refreshed : r));
      setRows(next);
      const recount: Record<string, number> = { healthy: 0, degraded: 0, not_tested: 0 };
      next.forEach((r) => {
        const s = (r.metadata_json?.status as string) || "not_tested";
        recount[s] = (recount[s] || 0) + 1;
      });
      setCounts(recount);
    } finally {
      setProbing(null);
    }
  };

  return (
    <div className="mt-5 rounded-lg border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
          <Link2 className="h-4 w-4 text-brand" />
          Integration Health
        </span>
        <div className="flex items-center gap-1.5">
          {(["healthy", "degraded", "not_tested"] as const).map((k) =>
            counts[k] ? (
              <Pill key={k} tone={HEALTH_TONE[k]} className="!text-[10.5px]">
                {counts[k]} {HEALTH_LABEL[k]}
              </Pill>
            ) : null
          )}
        </div>
      </div>
      <table className="table-shell !text-[12px]">
        <thead>
          <tr>
            <th>Integration</th>
            <th>Type</th>
            <th>Direction</th>
            <th>Status</th>
            <th>Last seen</th>
            <th className="text-right"></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const md = row.metadata_json || {};
            const status = (md.status as string) || "not_tested";
            const isProbing = probing === row.id;
            return (
              <tr key={row.id}>
                <td className="font-medium text-ink">{row.name}</td>
                <td className="font-mono text-[11px] text-ink-muted">{md.transport || "—"}</td>
                <td className="text-[11px] text-ink-muted">{md.direction || "—"}</td>
                <td>
                  <Pill tone={HEALTH_TONE[status]} className="!text-[10.5px]">
                    {HEALTH_LABEL[status] || status}
                  </Pill>
                </td>
                <td className="text-[11px] text-ink-muted">
                  {row.last_used_at ? formatDate(row.last_used_at) : "—"}
                </td>
                <td className="text-right">
                  <button
                    onClick={() => reprobe(row)}
                    disabled={isProbing}
                    className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[10.5px] font-medium text-ink-muted hover:border-brand hover:text-brand-dark disabled:opacity-60"
                    title="Re-probe this integration"
                  >
                    {isProbing ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    Re-probe
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const DrilldownModal: React.FC<{
  runId: number;
  pillar: PillarSpec["code"];
  spec: PillarSpec;
  onClose: () => void;
}> = ({ runId, pillar, spec, onClose }) => {
  const [rows, setRows] = useState<DiscoveredObject[] | null>(null);
  const [riskFilter, setRiskFilter] = useState<string>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [contextFilter, setContextFilter] = useState<string>("all");

  useEffect(() => {
    DiscoveryApi.objects(runId, { pillar, limit: 1000 }).then(setRows);
  }, [runId, pillar]);

  const visible = useMemo(() => {
    if (!rows) return null;
    return rows.filter((r) => {
      if (riskFilter !== "all" && r.risk_level !== riskFilter) return false;
      if (categoryFilter !== "all" && r.category !== categoryFilter) return false;
      if (contextFilter !== "all" && (r.metadata_json?.context_bucket as string) !== contextFilter) return false;
      return true;
    });
  }, [rows, riskFilter, categoryFilter, contextFilter]);

  const Icon = spec.icon;

  const riskCounts = useMemo(() => {
    const out: Record<string, number> = { low: 0, medium: 0, high: 0 };
    (rows || []).forEach((r) => {
      const k = (r.risk_level || "low").toLowerCase();
      if (out[k] !== undefined) out[k]++;
    });
    return out;
  }, [rows]);

  const categoryCounts = useMemo(() => {
    const out: Record<string, number> = {};
    (rows || []).forEach((r) => {
      out[r.category] = (out[r.category] || 0) + 1;
    });
    return out;
  }, [rows]);

  const contextCounts = useMemo(() => {
    const out: Record<string, number> = {};
    (rows || []).forEach((r) => {
      const bucket = (r.metadata_json?.context_bucket as string) || null;
      if (bucket) out[bucket] = (out[bucket] || 0) + 1;
    });
    return out;
  }, [rows]);

  // Group visible rows by at_risk_group so the table renders cluster
  // headers ("Customer Trade Profile · 12 fields") like the Bolt screenshot.
  const grouped = useMemo(() => {
    if (!visible) return null;
    const map = new Map<string, DiscoveredObject[]>();
    for (const r of visible) {
      const group = (r.metadata_json?.at_risk_group as string) || r.category;
      if (!map.has(group)) map.set(group, []);
      map.get(group)!.push(r);
    }
    return Array.from(map.entries());
  }, [visible]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`${spec.label} drilldown`}
      size="lg"
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-subtle">
            <Icon className="h-4 w-4 text-brand-dark" />
          </div>
          <div className="text-[12.5px] text-ink-muted">
            {spec.subtitle}. Drilldown surfaces individual discovered objects
            with risk classification, the at-risk group they cluster into, and
            the proposed Fusion target.
          </div>
        </div>

        {/* Filter row: risk / category / context */}
        {rows && rows.length > 0 && (
          <div className="space-y-1.5">
            <FilterRow
              label="Risk"
              value={riskFilter}
              options={[
                { v: "all",    label: `All · ${rows.length}` },
                { v: "high",   label: `High · ${riskCounts.high}` },
                { v: "medium", label: `Medium · ${riskCounts.medium}` },
                { v: "low",    label: `Low · ${riskCounts.low}` },
              ]}
              onChange={setRiskFilter}
            />
            {Object.keys(categoryCounts).length > 1 && (
              <FilterRow
                label="Category"
                value={categoryFilter}
                options={[
                  { v: "all", label: `All categories · ${rows.length}` },
                  ...Object.entries(categoryCounts).map(([c, n]) => ({
                    v: c, label: `${c} · ${n}`,
                  })),
                ]}
                onChange={setCategoryFilter}
              />
            )}
            {Object.keys(contextCounts).length > 0 && (
              <FilterRow
                label="Context"
                value={contextFilter}
                options={[
                  { v: "all", label: `All contexts` },
                  ...Object.entries(contextCounts).map(([c, n]) => ({
                    v: c, label: `${c} · ${n}`,
                  })),
                ]}
                onChange={setContextFilter}
              />
            )}
          </div>
        )}

        {!grouped ? (
          <div className="text-xs text-ink-muted">
            <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />
            Loading objects…
          </div>
        ) : grouped.length === 0 ? (
          <EmptyState
            icon={<CheckCircle2 className="h-5 w-5" />}
            title="No objects match this filter"
            description="Try All to see the full inventory for this pillar."
          />
        ) : (
          <div className="space-y-3">
            {grouped.map(([groupName, groupRows]) => (
              <GroupTable
                key={groupName}
                groupName={groupName}
                rows={groupRows}
              />
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
};

const FilterRow: React.FC<{
  label: string;
  value: string;
  options: { v: string; label: string }[];
  onChange: (v: string) => void;
}> = ({ label, value, options, onChange }) => (
  <div className="flex flex-wrap items-center gap-1.5">
    <span className="w-[60px] text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
      {label}
    </span>
    {options.map((opt) => (
      <button
        key={opt.v}
        onClick={() => onChange(opt.v)}
        className={cn(
          "rounded-md border border-line bg-white px-2 py-0.5 text-[11px] font-medium",
          value === opt.v ? "border-brand text-brand-dark" : "text-ink-muted hover:text-ink"
        )}
      >
        {opt.label}
      </button>
    ))}
  </div>
);

const GroupTable: React.FC<{
  groupName: string;
  rows: DiscoveredObject[];
}> = ({ groupName, rows }) => {
  const sample = rows[0]?.metadata_json || {};
  return (
    <div className="rounded-md border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line bg-canvas px-3 py-1.5">
        <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-ink">
          <span className="rounded bg-brand-subtle px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-brand-dark">
            {sample.context_bucket || rows[0]?.category || "GROUP"}
          </span>
          {groupName}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-ink-muted">
          {rows.length} object{rows.length === 1 ? "" : "s"}
        </span>
      </div>
      <table className="table-shell !text-[12px]">
        <thead>
          <tr>
            <th>Object</th>
            <th>Risk</th>
            <th>Fusion target</th>
            <th>Why it's flagged</th>
            <th className="text-right">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const md = row.metadata_json || {};
            return (
              <tr key={row.id}>
                <td className="font-mono text-[11.5px] text-ink">{row.name}</td>
                <td><RiskPill risk={row.risk_level || "low"} /></td>
                <td className="text-[11px] text-ink-muted">{md.fusion_target || "—"}</td>
                <td className="text-[11px] text-ink-muted">{md.risk_reason || renderDetailLine(md)}</td>
                <td className="text-right text-[11px] text-ink-muted">
                  {row.last_used_at ? formatDate(row.last_used_at) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const RiskPill: React.FC<{ risk: string }> = ({ risk }) => {
  const tone =
    risk === "high"   ? "danger" :
    risk === "medium" ? "warning" :
    risk === "low"    ? "success" : "neutral";
  return (
    <Pill tone={tone} className="!text-[10.5px]">
      {risk}
    </Pill>
  );
};

function renderDetailLine(md: Record<string, any>): string {
  if (md.row_count) return `${md.row_count.toLocaleString()} rows · ${md.table || ""}`.trim();
  if (md.count) return `${md.count.toLocaleString()} ${md.count === 1 ? "object" : "objects"}`;
  if (md.transport) return `${md.transport} · ${md.direction || ""}`.trim();
  if (md.high_risk !== undefined) {
    return `${md.high_risk} high · ${md.medium_risk} med · ${md.low_risk} low`;
  }
  if (md.segments) return `${md.segments}-segment`;
  return "";
}
