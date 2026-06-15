import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Play, RefreshCw, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from "recharts";
import { LoadApi, ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate } from "@/lib/utils";
import type {
  Conversion,
  LoadError,
  LoadRun,
  LoadSummary,
  Project,
} from "@/types";

// Distinct error-category palette — purposeful, used to encode meaning in charts
const CAT_COLORS = ["#EF4444", "#F59E0B", "#3B82F6", "#8B5CF6", "#10B981", "#EC4899", "#64748B"];

export const LoadDashboardPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  // Project layer first — the URL carries both ``project`` and
  // ``conversion`` so deep-links survive refresh. We never default
  // ``conversion`` blindly to ``[0]`` across projects; the picker is
  // always scoped to the selected engagement.
  const [projects, setProjects] = useState<Project[]>([]);
  const [conversions, setConversions] = useState<Conversion[]>([]);
  const [projectId, setProjectId] = useState<number | null>(
    params.get("project") ? Number(params.get("project")) : null,
  );
  const [pid, setPid] = useState<number | null>(
    params.get("conversion") ? Number(params.get("conversion")) : null,
  );
  const [runs, setRuns] = useState<LoadRun[]>([]);
  const [summary, setSummary] = useState<LoadSummary | null>(null);
  const [errors, setErrors] = useState<LoadError[]>([]);
  const [running, setRunning] = useState(false);

  // Load projects once; default the engagement if not URL-pinned.
  useEffect(() => {
    ProjectsApi.list().then((rows) => {
      setProjects(rows);
      if (!projectId && rows[0]) setProjectId(rows[0].id);
    });
  }, []);

  // Load this project's conversions; default conversion if not pinned
  // or if the pinned conversion belongs to a different project.
  useEffect(() => {
    if (!projectId) { setConversions([]); return; }
    ProjectsApi.conversions(projectId).then((rows) => {
      setConversions(rows);
      const pinnedBelongsToProject = !!rows.find((c) => c.id === pid);
      if (!pinnedBelongsToProject) {
        const first = rows[0];
        setPid(first ? first.id : null);
        if (first) setParams({ project: String(projectId), conversion: String(first.id) });
      } else {
        setParams({ project: String(projectId), conversion: String(pid) });
      }
    });
  }, [projectId]);

  const refresh = async () => {
    if (!pid) { setSummary(null); setRuns([]); setErrors([]); return; }
    setSummary(null); setRuns([]); setErrors([]);
    const [rs, sm] = await Promise.all([
      LoadApi.runs(pid),
      LoadApi.summary(pid).catch(() => null),
    ]);
    setRuns(rs);
    setSummary(sm);
    if (rs[0]) setErrors(await LoadApi.errors(rs[0].id));
  };
  useEffect(() => { refresh(); }, [pid]);

  const loadToFusion = async () => {
    if (!pid) return;
    setRunning(true);
    try { await LoadApi.simulate(pid); await refresh(); }
    finally { setRunning(false); }
  };

  const project = projects.find((p) => p.id === projectId) || null;
  const conversion = conversions.find((c) => c.id === pid) || null;

  const passFailData = useMemo(() => summary ? [
    { name: "Passed", value: summary.passed_count, color: "#10B981" },
    { name: "Warnings", value: summary.warning_count, color: "#F59E0B" },
    { name: "Failed", value: summary.failed_count, color: "#EF4444" },
  ] : [], [summary]);

  return (
    <>
      <PageTitle
        title="Load Management"
        subtitle="Run Fusion loads per engagement and inspect failures by category & root cause"
        right={<Button onClick={loadToFusion} loading={running} disabled={!pid}>
          <Play className="h-4 w-4" /> Load to Fusion
        </Button>}
      />

      <Card className="mb-4">
        <CardBody className="!py-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="label !mb-0">Engagement</label>
            <select
              className="input !w-auto min-w-[260px]"
              value={projectId ?? ""}
              onChange={(e) => setProjectId(Number(e.target.value))}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.client ? ` · ${p.client}` : ""}
                </option>
              ))}
            </select>
            <label className="label !mb-0 ml-2">Object</label>
            <select
              className="input !w-auto min-w-[260px]"
              value={pid ?? ""}
              onChange={(e) => {
                const v = Number(e.target.value);
                setPid(v);
                setParams({ project: String(projectId || ""), conversion: String(v) });
              }}
            >
              {conversions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.target_object})
                </option>
              ))}
            </select>
            <Button variant="secondary" onClick={refresh} disabled={!pid}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            {project && (
              <span className="ml-auto text-[11px] text-ink-muted">
                Source: <span className="font-mono text-ink">{project.source_system || "—"}</span>
                {Array.isArray(project.selected_modules) && project.selected_modules.length > 0 && (
                  <> · Scope: <span className="text-ink">{project.selected_modules.join(", ")}</span></>
                )}
              </span>
            )}
          </div>
        </CardBody>
      </Card>

      {!conversion ? (
        <Card>
          <CardBody><EmptyState
            title="Pick an engagement to begin"
            description="Load Management runs in the context of one engagement at a time. Each engagement has its own conversion list."
          /></CardBody>
        </Card>
      ) : (!summary || summary.total_records === 0) ? (
        <Card>
          <CardBody><EmptyState
            title="No load runs yet"
            description="Click Load to Fusion to run validation through the load engine and see pass/fail metrics."
            action={<Button onClick={loadToFusion} loading={running}><Play className="h-4 w-4" /> Load to Fusion</Button>}
          /></CardBody>
        </Card>
      ) : (
        <>
          {/* Top KPI strip */}
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <KpiBadge label="Total Records" value={summary.total_records} />
            <KpiBadge label="Passed" value={summary.passed_count} icon={CheckCircle2} tone="success" />
            <KpiBadge label="Failed" value={summary.failed_count} icon={XCircle} tone="danger" />
            <KpiBadge label="Warnings" value={summary.warning_count} icon={AlertTriangle} tone="warning" />
          </div>

          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* Pass / fail chart */}
            <Card>
              <CardHeader title="Pass / Fail Distribution" />
              <CardBody>
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={passFailData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
                      {passFailData.map((d, i) => <Cell key={i} fill={d.color} />)}
                    </Pie>
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </CardBody>
            </Card>

            {/* Error categories chart */}
            <Card className="lg:col-span-2">
              <CardHeader title="Error Categories" subtitle="Distribution of failures by category" />
              <CardBody>
                {summary.error_categories.length === 0 ? (
                  <EmptyState title="No errors" description="All records passed validation." />
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={summary.error_categories} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 160 }}>
                      <CartesianGrid stroke="#F1F5F9" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} stroke="#94A3B8" fontSize={11} />
                      <YAxis type="category" dataKey="name" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} width={150} />
                      <Tooltip contentStyle={{ fontSize: 12 }} />
                      <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                        {summary.error_categories.map((_, i) => <Cell key={i} fill={CAT_COLORS[i % CAT_COLORS.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </CardBody>
            </Card>
          </div>

          {/* Root causes + dependencies */}
          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Root Causes" subtitle={`${summary.root_causes.length} unique cause(s)`} />
              {summary.root_causes.length === 0 ? <CardBody><EmptyState title="No causes recorded" /></CardBody> :
                <table className="table-shell">
                  <thead><tr><th>Cause</th><th className="text-right">Count</th></tr></thead>
                  <tbody>
                    {summary.root_causes.map((c, i) => (
                      <tr key={i}>
                        <td className="text-ink">{c.cause}</td>
                        <td className="text-right tabular-nums text-ink-muted">{c.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            </Card>
            <Card>
              <CardHeader title="Dependency Impact" subtitle="Upstream objects driving these failures" />
              {summary.dependency_impacts.length === 0 ? <CardBody><EmptyState title="No dependency impacts" /></CardBody> :
                <table className="table-shell">
                  <thead><tr><th>Object</th><th className="text-right">Impacted</th></tr></thead>
                  <tbody>
                    {summary.dependency_impacts.map((d, i) => (
                      <tr key={i}>
                        <td><Pill tone="warning">{d.object}</Pill></td>
                        <td className="text-right tabular-nums text-ink-muted">{d.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            </Card>
          </div>

          {/* Error grid */}
          <Card>
            <CardHeader title="Latest Run Errors" subtitle={runs[0] ? `Run #${runs[0].id} · ${formatDate(runs[0].started_at)}` : "—"} />
            {errors.length === 0 ? <CardBody><EmptyState title="No errors recorded" /></CardBody> : (
              <div className="overflow-x-auto">
                <table className="table-shell">
                  <thead>
                    <tr>
                      <th>Row</th><th>Field</th><th>Category</th>
                      <th>Message</th><th>Root Cause</th>
                      <th>Dependency</th><th>Suggested Fix</th>
                    </tr>
                  </thead>
                  <tbody>
                    {errors.slice(0, 200).map(e => (
                      <tr key={e.id}>
                        <td className="text-ink-muted">{e.row_number ?? "—"}</td>
                        <td className="font-medium">{e.object_name || "—"}</td>
                        <td><Pill tone="danger">{e.error_category}</Pill></td>
                        <td className="max-w-[320px] truncate" title={e.error_message || ""}>{e.error_message || "—"}</td>
                        <td className="max-w-[280px] truncate text-ink-muted">{e.root_cause || "—"}</td>
                        <td className="text-ink-muted">{e.related_dependency || "—"}</td>
                        <td className="max-w-[280px] truncate text-ink-muted">{e.suggested_fix || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
};

const KpiBadge: React.FC<{ label: string; value: number; icon?: React.ElementType; tone?: "success" | "danger" | "warning" }> =
  ({ label, value, icon: Icon, tone }) => {
    const text = tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-ink";
    return (
      <div className="card p-3">
        <div className="flex items-center justify-between">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">{label}</div>
          {Icon && <Icon className={`h-4 w-4 ${text}`} />}
        </div>
        <div className={`mt-1 text-2xl font-semibold tabular-nums ${text}`}>{value}</div>
      </div>
    );
  };
