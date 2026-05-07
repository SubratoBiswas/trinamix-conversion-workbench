import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Play, RefreshCw, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from "recharts";
import { ConversionsApi, LoadApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate } from "@/lib/utils";
import type {
  Conversion,
  LoadError,
  LoadRun,
  LoadSummary,
} from "@/types";

// Distinct error-category palette — purposeful, used to encode meaning in charts
const CAT_COLORS = ["#EF4444", "#F59E0B", "#3B82F6", "#8B5CF6", "#10B981", "#EC4899", "#64748B"];

export const LoadDashboardPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");
  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<number | null>(projParam ? Number(projParam) : null);
  const [runs, setRuns] = useState<LoadRun[]>([]);
  const [summary, setSummary] = useState<LoadSummary | null>(null);
  const [errors, setErrors] = useState<LoadError[]>([]);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    ConversionsApi.list().then((ps) => {
      setProjects(ps);
      if (!pid && ps[0]) {
        setPid(ps[0].id);
        setParams({ conversion: String(ps[0].id) });
      }
    });
  }, []);

  const refresh = async () => {
    if (!pid) return;
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

  const simulate = async () => {
    if (!pid) return;
    setRunning(true);
    try { await LoadApi.simulate(pid); await refresh(); }
    finally { setRunning(false); }
  };

  const passFailData = useMemo(() => summary ? [
    { name: "Passed", value: summary.passed_count, color: "#10B981" },
    { name: "Warnings", value: summary.warning_count, color: "#F59E0B" },
    { name: "Failed", value: summary.failed_count, color: "#EF4444" },
  ] : [], [summary]);

  return (
    <>
      <PageTitle
        title="Load Runs"
        subtitle="Simulate Fusion loads and inspect failures by category & root cause"
        right={<Button onClick={simulate} loading={running} disabled={!pid}>
          <Play className="h-4 w-4" /> Simulate Load
        </Button>}
      />

      <Card className="mb-4">
        <CardBody className="!py-3">
          <div className="flex items-center gap-3">
            <label className="label !mb-0">Project</label>
            <select className="input !w-auto min-w-[280px]" value={pid ?? ""}
              onChange={(e) => { const v = Number(e.target.value); setPid(v); setParams({ conversion: String(v) }); }}>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button variant="secondary" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button>
          </div>
        </CardBody>
      </Card>

      {!summary || summary.total_records === 0 ? (
        <Card>
          <CardBody><EmptyState
            title="No load runs yet"
            description="Click Simulate Load to run validation through the load engine and see pass/fail metrics."
            action={<Button onClick={simulate} loading={running}><Play className="h-4 w-4" /> Simulate Load</Button>}
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
