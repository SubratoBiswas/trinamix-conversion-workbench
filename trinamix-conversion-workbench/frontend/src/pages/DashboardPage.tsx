import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Database, FileSpreadsheet, Boxes, Workflow as WfIcon,
  CloudUpload, TrendingUp, TrendingDown, ArrowRight,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell,
  PieChart, Pie, Legend,
} from "recharts";
import { DashboardApi } from "@/api";
import { Card, CardHeader, CardBody, PageTitle, PageLoader, Pill, EmptyState } from "@/components/ui/Primitives";
import { formatDate, statusTone } from "@/lib/utils";
import type { DashboardKpis } from "@/types";

const KpiCard: React.FC<{
  title: string;
  value: React.ReactNode;
  icon: React.ElementType;
  delta?: { value: string; tone: "success" | "danger" | "neutral" };
  helper?: string;
}> = ({ title, value, icon: Icon, delta, helper }) => (
  <div className="card p-4">
    <div className="flex items-start justify-between">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">{title}</div>
      <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-subtle text-brand">
        <Icon className="h-4 w-4" />
      </div>
    </div>
    <div className="mt-2 text-3xl font-semibold tabular-nums text-ink">{value}</div>
    <div className="mt-1.5 flex items-center gap-1 text-xs">
      {delta && (
        <span className={
          delta.tone === "success" ? "text-success" :
          delta.tone === "danger" ? "text-danger" : "text-ink-muted"
        }>
          {delta.tone === "success" ? <TrendingUp className="inline h-3 w-3" /> :
           delta.tone === "danger" ? <TrendingDown className="inline h-3 w-3" /> : null}
          {" "}{delta.value}
        </span>
      )}
      {helper && <span className="text-ink-muted">{helper}</span>}
    </div>
  </div>
);

const STATUS_COLORS: Record<string, string> = {
  draft: "#94A3B8",
  mapping_suggested: "#3B82F6",
  awaiting_approval: "#F59E0B",
  validated: "#6366F1",
  output_generated: "#8B5CF6",
  loaded: "#10B981",
  failed: "#EF4444",
  completed: "#10B981",
  running: "#F59E0B",
};

export const DashboardPage: React.FC = () => {
  const [kpis, setKpis] = useState<DashboardKpis | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    DashboardApi.kpis().then(setKpis).finally(() => setLoading(false));
  }, []);

  if (loading) return <PageLoader />;
  if (!kpis) return <EmptyState title="No data" description="Failed to load dashboard." />;

  const projectChartData = kpis.project_status_breakdown.map(s => ({
    ...s,
    fill: STATUS_COLORS[s.status] || "#64748B",
  }));

  return (
    <>
      <PageTitle
        title="Dashboard"
        subtitle={`Conversion overview · ${new Date().toLocaleDateString()}`}
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        <KpiCard title="Datasets" value={kpis.total_datasets} icon={Database} helper="Source extracts loaded" />
        <KpiCard title="FBDI Templates" value={kpis.total_templates} icon={FileSpreadsheet} helper="Target objects" />
        <KpiCard title="Conversion Projects" value={kpis.total_projects} icon={Boxes} helper="Active + complete" />
        <KpiCard title="Workflows" value={kpis.total_workflows} icon={WfIcon} helper="Saved dataflows" />
        <KpiCard
          title="Load Pass Rate"
          value={`${kpis.pass_rate}%`}
          icon={CloudUpload}
          delta={kpis.pass_rate >= 80
            ? { value: `${kpis.pass_rate}% passed`, tone: "success" }
            : { value: `${kpis.fail_rate}% failed`, tone: "danger" }}
        />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Project status */}
        <Card className="lg:col-span-2">
          <CardHeader title="Project Status Breakdown" subtitle="Distribution across the conversion lifecycle" />
          <CardBody>
            {projectChartData.length === 0 ? (
              <EmptyState title="No projects yet" description="Create your first conversion project to see status." />
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={projectChartData} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 110 }}>
                  <XAxis type="number" allowDecimals={false} stroke="#94A3B8" fontSize={11} />
                  <YAxis type="category" dataKey="status" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ fontSize: 12, border: "1px solid #E2E8F0", borderRadius: 8 }} />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {projectChartData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardBody>
        </Card>

        {/* Load runs */}
        <Card>
          <CardHeader title="Load Runs" subtitle={`${kpis.total_load_runs} total`} />
          <CardBody>
            {kpis.load_status_breakdown.length === 0 ? (
              <EmptyState title="No load runs" description="Simulate a load from a project." />
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={kpis.load_status_breakdown}
                    dataKey="count"
                    nameKey="status"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={2}
                  >
                    {kpis.load_status_breakdown.map((s, i) => (
                      <Cell key={i} fill={STATUS_COLORS[s.status] || "#64748B"} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardBody>
        </Card>
      </div>

      {/* Recent */}
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Recent Projects" actions={<Link to="/projects" className="text-xs text-brand-dark hover:underline">View all <ArrowRight className="inline h-3 w-3" /></Link>} />
          <div>
            {kpis.recent_projects.length === 0 ? (
              <CardBody><EmptyState title="No projects yet" /></CardBody>
            ) : (
              <table className="table-shell">
                <thead>
                  <tr><th>Name</th><th>Status</th><th>Updated</th></tr>
                </thead>
                <tbody>
                  {kpis.recent_projects.map((p) => (
                    <tr key={p.id} className="cursor-pointer">
                      <td>
                        <Link to={`/projects/${p.id}`} className="font-medium text-ink hover:text-brand-dark">{p.name}</Link>
                      </td>
                      <td><Pill tone={statusTone(p.status)}>{p.status.replaceAll("_", " ")}</Pill></td>
                      <td className="text-ink-muted">{formatDate(p.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Recent Load Runs" actions={<Link to="/load" className="text-xs text-brand-dark hover:underline">View all <ArrowRight className="inline h-3 w-3" /></Link>} />
          <div>
            {kpis.recent_load_runs.length === 0 ? (
              <CardBody><EmptyState title="No load runs" /></CardBody>
            ) : (
              <table className="table-shell">
                <thead>
                  <tr><th>Run</th><th>Status</th><th>Records</th><th>Pass / Fail</th></tr>
                </thead>
                <tbody>
                  {kpis.recent_load_runs.map((r) => (
                    <tr key={r.id}>
                      <td className="text-ink-muted">#{r.id}</td>
                      <td><Pill tone={statusTone(r.status)}>{r.status}</Pill></td>
                      <td className="tabular-nums">{r.total_records}</td>
                      <td className="tabular-nums">
                        <span className="text-success">{r.passed_count}</span>
                        {" / "}
                        <span className="text-danger">{r.failed_count}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>
    </>
  );
};
