import React, { useEffect, useMemo, useState } from "react";
import {
  Sparkles, Database, Cog, Workflow, Zap, FileBarChart, Cable,
  RefreshCw, AlertTriangle, CheckCircle2, ChevronRight, X,
  ShieldCheck, Loader2, Link2, Activity, FlaskConical,
} from "lucide-react";
import { api } from "@/api/client";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";

/**
 * Discovery panel — embedded inside Project Overview.
 *
 * Uses the project-scoped API:
 *   GET  /api/projects/{id}/discovery/latest  → { run, integrations }
 *   POST /api/projects/{id}/discovery/run     → DiscoveryRunDetail
 *   GET  /api/discovery-runs/{id}/objects     → DiscoveredObject[]
 *
 * Works for BOTH mock-mode and live Oracle EBS connections.
 * The run.is_mock flag drives the informational banner.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

interface DiscoveryRunDetail {
  id: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  total_objects: number;
  pillar_counts: Record<string, number>;
  integration_health: Record<string, number>;
  complexity_score: number;
  scan_notes?: string | null;
  is_mock: boolean;
}

interface DiscoveryLatest {
  run: DiscoveryRunDetail | null;
  integrations: any[];
}

interface DiscoveredObj {
  id: string;
  pillar: string;
  category: string;
  name: string;
  risk_level?: string | null;
  last_used_at?: string | null;
  metadata_json?: Record<string, any>;
}

// ── Pillar config ──────────────────────────────────────────────────────────────

interface PillarSpec {
  code: "data" | "configuration" | "processes" | "customisations" | "reports" | "integrations";
  label: string;
  icon: React.ElementType;
  accent: string;
  subtitle: string;
}

const PILLARS: PillarSpec[] = [
  { code: "data",           label: "Data",           icon: Database,     accent: "border-info/60",    subtitle: "records × entity types" },
  { code: "configuration",  label: "Configuration",  icon: Cog,          accent: "border-brand/60",   subtitle: "setup objects" },
  { code: "processes",      label: "Processes",      icon: Workflow,     accent: "border-warning/60", subtitle: "workflows & approvals" },
  { code: "customisations", label: "Customisations", icon: Zap,          accent: "border-danger/60",  subtitle: "scripts & custom fields" },
  { code: "reports",        label: "Reports",        icon: FileBarChart, accent: "border-success/60", subtitle: "saved searches & reports" },
  { code: "integrations",   label: "Integrations",   icon: Cable,        accent: "border-info/60",    subtitle: "interfaces & APIs" },
];

// ── Panel ──────────────────────────────────────────────────────────────────────

export const DiscoveryPanel: React.FC<{
  projectId: string;
  hasConnection: boolean;
}> = ({ projectId, hasConnection }) => {
  const [latest, setLatest] = useState<DiscoveryLatest | null | undefined>(undefined);
  const [running, setRunning] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<PillarSpec["code"] | null>(null);

  const reload = async () => {
    try {
      const data = await api
        .get<DiscoveryLatest>(`/projects/${projectId}/discovery/latest`)
        .then(r => r.data);
      setLatest(data);
    } catch {
      setLatest({ run: null, integrations: [] });
    }
  };

  useEffect(() => { reload(); }, [projectId]);

  const runScan = async () => {
    setRunning(true);
    setError(null);
    try {
      await api.post(`/projects/${projectId}/discovery/run`);
      await reload();
    } catch (e: any) {
      setError(
        e?.response?.data?.detail ||
        e?.message ||
        "Discovery scan failed"
      );
    } finally {
      setRunning(false);
    }
  };

  // Loading state
  if (latest === undefined) {
    return (
      <Card className="mt-4">
        <CardHeader title="Discovery" subtitle="Loading…" />
        <CardBody>
          <Loader2 className="h-4 w-4 animate-spin text-ink-muted" />
        </CardBody>
      </Card>
    );
  }

  // No scan yet
  if (!latest?.run) {
    return (
      <Card className="mt-4">
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <Sparkles className="h-4 w-4 text-brand" /> Discovery
            </span>
          }
          subtitle="Source-system inventory — customisations, integrations, processes, master data."
        />
        <CardBody>
          {hasConnection ? (
            <EmptyState
              icon={<Activity className="h-5 w-5" />}
              title="No discovery scan yet"
              description="Run a discovery scan to inventory every customisation, integration, process, and master-data entity. Mock-mode scans return instantly with deterministic fixtures."
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

  return (
    <>
      <Card className="mt-4">
        <CardHeader
          title={
            <span className="inline-flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-brand" />
              Discovery
              {run.is_mock && (
                <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-[10px] font-semibold text-warning-dark">
                  <FlaskConical className="h-3 w-3" /> Mock
                </span>
              )}
            </span>
          }
          subtitle={
            <span className="text-xs text-ink-muted">
              {(run.total_objects ?? 0).toLocaleString()} objects across 6 pillars · complexity{" "}
              <span className="font-semibold text-ink">{Math.round(run.complexity_score ?? 0)}</span>/100
              {run.completed_at ? ` · last scan ${formatDate(run.completed_at)}` : ""}
            </span>
          }
          actions={
            <Button onClick={runScan} loading={running} variant="secondary" className="!h-8 !text-xs">
              <RefreshCw className="h-3.5 w-3.5" />
              {run.is_mock ? "Re-scan (mock)" : "Re-scan (live)"}
            </Button>
          }
        />
        <CardBody>
          {/* Mock mode info banner */}
          {run.is_mock && (
            <div className="mb-4 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-[11.5px] text-warning-dark">
              <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <strong>Mock mode active</strong> — results are deterministic fixtures, not live EBS data.
                To scan your real instance, open the Source Connection card above and uncheck <em>Use mock mode</em>.
              </span>
            </div>
          )}

          {/* 6 pillar cards */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            {PILLARS.map((p) => (
              <PillarTile
                key={p.code}
                spec={p}
                count={run.pillar_counts?.[p.code] ?? 0}
                onClick={() => setDrilldown(p.code)}
              />
            ))}
          </div>

          {/* Scan notes */}
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

      {drilldown && (
        <DrilldownModal
          runId={run.id}
          pillar={drilldown}
          spec={PILLARS.find((p) => p.code === drilldown)!}
          isMock={run.is_mock}
          onClose={() => setDrilldown(null)}
        />
      )}
    </>
  );
};

// ── Pillar tile ────────────────────────────────────────────────────────────────

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
        {(count ?? 0).toLocaleString()}
      </div>
      <div className="text-[11.5px] font-semibold text-ink">{spec.label}</div>
      <div className="text-[10.5px] text-ink-muted">{spec.subtitle}</div>
    </button>
  );
};

// ── Drilldown modal ────────────────────────────────────────────────────────────

const DrilldownModal: React.FC<{
  runId: string;
  pillar: PillarSpec["code"];
  spec: PillarSpec;
  isMock: boolean;
  onClose: () => void;
}> = ({ runId, pillar, spec, isMock, onClose }) => {
  const [rows, setRows]                 = useState<DiscoveredObj[] | null>(null);
  const [riskFilter, setRiskFilter]     = useState<string>("all");
  const [loadErr, setLoadErr]           = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    setLoadErr(null);
    api
      .get<DiscoveredObj[]>(`/discovery-runs/${runId}/objects`, {
        params: { pillar },
      })
      .then(r => setRows(r.data))
      .catch(() => {
        setRows([]);
        setLoadErr("Could not load objects for this pillar.");
      });
  }, [runId, pillar]);

  const riskCounts = useMemo(() => {
    const out: Record<string, number> = { low: 0, medium: 0, high: 0 };
    (rows || []).forEach((r) => {
      const k = (r.risk_level || "low").toLowerCase();
      if (out[k] !== undefined) out[k]++;
    });
    return out;
  }, [rows]);

  const visible = useMemo(() => {
    if (!rows) return null;
    return riskFilter === "all"
      ? rows
      : rows.filter((r) => (r.risk_level || "low") === riskFilter);
  }, [rows, riskFilter]);

  // Group by at_risk_group (the EBS module)
  const grouped = useMemo(() => {
    if (!visible) return null;
    const map = new Map<string, DiscoveredObj[]>();
    for (const r of visible) {
      const group = (r.metadata_json?.at_risk_group as string) || r.category;
      if (!map.has(group)) map.set(group, []);
      map.get(group)!.push(r);
    }
    return Array.from(map.entries());
  }, [visible]);

  const Icon = spec.icon;

  return (
    <Modal
      open
      onClose={onClose}
      title={`${spec.label} drilldown`}
      size="lg"
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-subtle">
            <Icon className="h-4 w-4 text-brand-dark" />
          </div>
          <div className="text-[12.5px] text-ink-muted">
            {spec.subtitle}.
            {isMock ? " Showing mock fixture data." : " Showing data from live EBS scan."}
          </div>
        </div>

        {/* Risk filter */}
        {rows && rows.length > 0 && (
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
        )}

        {loadErr && (
          <div className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
            <AlertTriangle className="mr-1 inline h-3 w-3" /> {loadErr}
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
            title="No objects in this pillar"
            description={riskFilter !== "all" ? "Try All to see the full inventory." : "Run a discovery scan to populate this pillar."}
          />
        ) : (
          <div className="space-y-3">
            {grouped.map(([groupName, groupRows]) => (
              <GroupTable key={groupName} groupName={groupName} rows={groupRows} />
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
};

// ── Filter row ────────────────────────────────────────────────────────────────

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

// ── Group table ───────────────────────────────────────────────────────────────

const GroupTable: React.FC<{
  groupName: string;
  rows: DiscoveredObj[];
}> = ({ groupName, rows }) => {
  const sample = rows[0]?.metadata_json || {};
  return (
    <div className="rounded-md border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line bg-canvas px-3 py-1.5">
        <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-ink">
          <span className="rounded bg-brand-subtle px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-brand-dark">
            {sample.context_bucket || groupName}
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
            <th>Object / Table</th>
            <th>Type</th>
            <th>Risk</th>
            <th>Fusion target</th>
            <th className="text-right">Rows</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const md = row.metadata_json || {};
            return (
              <tr key={row.id}>
                <td className="font-mono text-[11.5px] text-ink">{row.name}</td>
                <td className="text-[11px] text-ink-muted capitalize">{row.category}</td>
                <td><RiskPill risk={row.risk_level || "low"} /></td>
                <td className="text-[11px] text-ink-muted">{md.fusion_target || "—"}</td>
                <td className="text-right font-mono text-[11px] tabular-nums text-ink-muted">
                  {md.row_count != null ? md.row_count.toLocaleString() : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

// ── Risk pill ──────────────────────────────────────────────────────────────────

const RiskPill: React.FC<{ risk: string }> = ({ risk }) => {
  const tone =
    risk === "high"   ? "danger"  :
    risk === "medium" ? "warning" :
    risk === "low"    ? "success" : "neutral";
  return (
    <Pill tone={tone} className="!text-[10.5px]">
      {risk}
    </Pill>
  );
};
