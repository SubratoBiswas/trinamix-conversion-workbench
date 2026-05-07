import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Plus, Boxes, Calendar, Building2, ArrowRight, ArrowLeft,
  CircleDot, CheckCircle2, AlertCircle, Clock,
} from "lucide-react";
import { ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader,
  PageTitle, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type { Project } from "@/types";

const STATUS_TONE: Record<string, "success" | "warning" | "info" | "neutral" | "danger"> = {
  planning:       "info",
  in_progress:    "warning",
  ready_for_uat:  "success",
  complete:       "success",
  on_hold:        "neutral",
};

/** List of implementation engagements (each contains 30+ conversion objects). */
export const ProjectsPage: React.FC = () => {
  const [items, setItems] = useState<Project[] | null>(null);
  useEffect(() => { ProjectsApi.list().then(setItems); }, []);

  return (
    <>
      <PageTitle
        title="Projects"
        subtitle="Implementation engagements — each contains many conversion objects"
        right={
          <Link to="/projects/new" className="btn-primary">
            <Plus className="h-4 w-4" /> New Engagement
          </Link>
        }
      />

      {items === null ? <PageLoader /> :
        items.length === 0 ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<Boxes className="h-5 w-5" />}
                title="No engagements yet"
                description="Create your first engagement (e.g. 'Acme SCM Cloud Phase 1') to start tracking conversion objects."
                action={
                  <Link to="/projects/new" className="btn-primary">
                    <Plus className="h-4 w-4" /> Create Engagement
                  </Link>
                }
              />
            </CardBody>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((p) => (
              <ProjectCard key={p.id} project={p} />
            ))}
          </div>
        )
      }
    </>
  );
};

const ProjectCard: React.FC<{ project: Project }> = ({ project }) => {
  const total = project.conversion_count ?? 0;
  const inProg = project.in_progress_count ?? 0;
  const loaded = project.loaded_count ?? 0;
  const failed = project.failed_count ?? 0;
  const pct = total > 0 ? Math.round((loaded / total) * 100) : 0;

  return (
    <Link
      to={`/projects/${project.id}`}
      className="group relative flex flex-col overflow-hidden rounded-lg border border-line bg-white transition hover:border-brand hover:shadow-soft"
    >
      <div className="px-5 py-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              <Building2 className="h-3 w-3" />
              {project.client || "—"}
            </div>
            <div className="mt-1 truncate text-[15px] font-semibold text-ink group-hover:text-brand-dark">
              {project.name}
            </div>
            {project.target_environment && (
              <div className="mt-0.5 truncate text-[11px] text-ink-muted">
                → {project.target_environment}
              </div>
            )}
          </div>
          <Pill tone={STATUS_TONE[project.status] || "neutral"}>{project.status.replace("_", " ")}</Pill>
        </div>

        {/* Progress bar */}
        {total > 0 && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-[10.5px]">
              <span className="font-mono tabular-nums text-ink">
                {loaded} / {total} loaded
              </span>
              <span className="font-mono tabular-nums text-ink-muted">{pct}%</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
              <div className="h-full rounded-full bg-success" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}

        {/* Object roll-ups */}
        <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[10.5px]">
          <Roll label="In progress" count={inProg} icon={<Clock className="h-3 w-3" />} tone="text-warning" />
          <Roll label="Loaded"      count={loaded} icon={<CheckCircle2 className="h-3 w-3" />} tone="text-success" />
          <Roll label="Failed"      count={failed} icon={<AlertCircle className="h-3 w-3" />} tone="text-danger" />
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-line bg-canvas px-5 py-2 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1">
          <Calendar className="h-3 w-3" />
          Go-live: {project.go_live_date ? formatDate(project.go_live_date) : "—"}
        </span>
        <span className="inline-flex items-center gap-1 font-medium text-brand-dark">
          Open <ArrowRight className="h-3 w-3" />
        </span>
      </div>
    </Link>
  );
};

const Roll: React.FC<{ label: string; count: number; icon: React.ReactNode; tone: string }> = ({ label, count, icon, tone }) => (
  <div className="rounded-md bg-canvas px-1.5 py-1.5">
    <div className={cn("flex items-center justify-center gap-1", tone)}>{icon}<span className="font-mono text-xs font-semibold tabular-nums">{count}</span></div>
    <div className="text-[9.5px] uppercase tracking-wider text-ink-muted">{label}</div>
  </div>
);

// ─────── New Engagement page ───────

export const NewProjectPage: React.FC = () => {
  const nav = useNavigate();
  const [body, setBody] = useState<Partial<Project>>({
    name: "", client: "", target_environment: "Oracle Fusion SCM Cloud",
    description: "", status: "planning",
  });
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!body.name) return;
    setBusy(true);
    try {
      const p = await ProjectsApi.create(body);
      nav(`/projects/${p.id}`);
    } finally { setBusy(false); }
  };

  return (
    <>
      <PageTitle
        title="New Engagement"
        subtitle="Create a new implementation engagement — you can add conversion objects after."
        right={
          <Link to="/projects" className="btn-ghost">
            <ArrowLeft className="h-4 w-4" /> Back
          </Link>
        }
      />
      <Card className="max-w-2xl">
        <CardBody>
          <div className="space-y-4">
            <div>
              <label className="label">Engagement name</label>
              <input className="input" placeholder="e.g. Acme — Oracle SCM Cloud Phase 1"
                value={body.name || ""} onChange={(e) => setBody({ ...body, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <label className="label">Client</label>
                <input className="input" placeholder="Acme Corp"
                  value={body.client || ""} onChange={(e) => setBody({ ...body, client: e.target.value })} />
              </div>
              <div>
                <label className="label">Target environment</label>
                <input className="input" placeholder="Oracle Fusion SCM Cloud"
                  value={body.target_environment || ""} onChange={(e) => setBody({ ...body, target_environment: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <label className="label">Go-live date (optional)</label>
                <input type="date" className="input"
                  value={body.go_live_date || ""}
                  onChange={(e) => setBody({ ...body, go_live_date: e.target.value || null })} />
              </div>
              <div>
                <label className="label">Status</label>
                <select className="input" value={body.status || "planning"}
                  onChange={(e) => setBody({ ...body, status: e.target.value })}>
                  <option value="planning">planning</option>
                  <option value="in_progress">in progress</option>
                  <option value="ready_for_uat">ready for UAT</option>
                  <option value="complete">complete</option>
                  <option value="on_hold">on hold</option>
                </select>
              </div>
            </div>
            <div>
              <label className="label">Description</label>
              <textarea className="input min-h-[80px]" placeholder="Scope notes, modules in play, special considerations…"
                value={body.description || ""} onChange={(e) => setBody({ ...body, description: e.target.value })} />
            </div>
            <div className="flex justify-end">
              <Button onClick={submit} loading={busy} disabled={!body.name}>
                Create Engagement
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
    </>
  );
};
