import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Plus, Layers, Database, FileSpreadsheet, ArrowRight,
} from "lucide-react";
import { ConversionsApi, ProjectsApi } from "@/api";
import {
  Card, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import type { Conversion, Project } from "@/types";

const STATUS_TONE = (s: string) => {
  if (s === "loaded" || s === "complete") return "success";
  if (s === "failed") return "danger";
  if (s === "planning") return "info";
  return "warning";
};

/** Cross-engagement view of every Conversion object. */
export const ConversionsPage: React.FC = () => {
  const [items, setItems] = useState<Conversion[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [filterProj, setFilterProj] = useState<number | "all">("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  useEffect(() => {
    Promise.all([
      ConversionsApi.list(),
      ProjectsApi.list(),
    ]).then(([cs, ps]) => { setItems(cs); setProjects(ps); });
  }, []);

  const visible = useMemo(() => {
    if (!items) return [];
    return items.filter(c => {
      if (filterProj !== "all" && c.project_id !== filterProj) return false;
      if (filterStatus !== "all" && c.status !== filterStatus) return false;
      return true;
    });
  }, [items, filterProj, filterStatus]);

  // Group conversions by project for display
  const grouped = useMemo(() => {
    const out: Record<number, { project: Project | undefined; rows: Conversion[] }> = {};
    for (const c of visible) {
      if (!out[c.project_id]) {
        out[c.project_id] = {
          project: projects.find(p => p.id === c.project_id),
          rows: [],
        };
      }
      out[c.project_id].rows.push(c);
    }
    return out;
  }, [visible, projects]);

  const allStatuses = useMemo(() =>
    Array.from(new Set((items || []).map(c => c.status))).sort(),
    [items]
  );

  if (items === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Conversion Objects"
        subtitle="All conversion objects across every engagement"
        right={
          <Link to="/projects" className="btn-ghost">
            Browse engagements →
          </Link>
        }
      />

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted">Filter:</span>
        <select className="input !h-8 !w-auto !text-xs"
          value={filterProj} onChange={(e) => setFilterProj(e.target.value === "all" ? "all" : Number(e.target.value))}>
          <option value="all">All engagements</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select className="input !h-8 !w-auto !text-xs"
          value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
          <option value="all">Any status</option>
          {allStatuses.map(s => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </select>
        <span className="ml-auto text-xs text-ink-muted">{visible.length} of {items.length} object(s)</span>
      </div>

      {visible.length === 0 ? (
        <Card>
          <div className="p-6">
            <EmptyState
              icon={<Layers className="h-5 w-5" />}
              title="No conversions match the filters"
              description="Pick a different engagement or status."
            />
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([pid, { project, rows }]) => (
            <Card key={pid}>
              <CardHeader
                title={
                  project ? (
                    <Link to={`/projects/${project.id}`} className="hover:text-brand-dark">
                      {project.name}
                    </Link>
                  ) : `Project #${pid}`
                }
                subtitle={`${rows.length} conversion${rows.length === 1 ? "" : "s"}${project?.client ? ` · ${project.client}` : ""}`}
                actions={project && <Pill tone="brand">{project.target_environment || "Fusion"}</Pill>}
              />
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>Object</th>
                    <th>Target</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th className="text-right">Load order</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <Link to={`/conversions/${c.id}`} className="font-medium text-ink hover:text-brand-dark">
                          {c.name}
                        </Link>
                        {c.target_object && (
                          <div className="text-[10.5px] text-ink-muted">→ {c.target_object}</div>
                        )}
                      </td>
                      <td>
                        {c.template_name ? (
                          <span className="inline-flex items-center gap-1 text-[12px]">
                            <FileSpreadsheet className="h-3 w-3 text-indigo-500" />
                            {c.template_name}
                          </span>
                        ) : <span className="text-ink-subtle italic">not selected</span>}
                      </td>
                      <td>
                        {c.dataset_name ? (
                          <span className="inline-flex items-center gap-1 text-[12px]">
                            <Database className="h-3 w-3 text-emerald-500" />
                            {c.dataset_name}
                          </span>
                        ) : <span className="text-ink-subtle italic">awaiting file</span>}
                      </td>
                      <td><Pill tone={STATUS_TONE(c.status)}>{c.status.replace("_", " ")}</Pill></td>
                      <td className="text-right font-mono tabular-nums text-[11px] text-ink-muted">{c.planned_load_order}</td>
                      <td className="text-right">
                        <Link to={`/conversions/${c.id}`} className="btn-ghost h-7 px-2 text-xs">
                          Open <ArrowRight className="h-3 w-3" />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      )}
    </>
  );
};
