import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Plus, Boxes, Calendar, Building2, ArrowRight, ArrowLeft,
  CheckCircle2, AlertCircle, Clock, Database,
} from "lucide-react";
import { ProjectsApi } from "@/api";
import {
  Card, CardBody, EmptyState, PageLoader,
  PageTitle, Pill,
} from "@/components/ui/Primitives";
import { SetupWizard } from "@/components/setup/SetupWizard";
import { cn, formatDate } from "@/lib/utils";
import type { Project } from "@/types";

// Code → display label mapping for the source-system pill on each project
// card. Kept in sync with backend/app/source_systems.py via the
// /api/source-systems endpoint at runtime; this is the static fallback.
const SOURCE_DISPLAY: Record<string, string> = {
  netsuite: "NetSuite",
  oracle_ebs: "Oracle EBS",
  sap_ecc: "SAP ECC",
  sap_s4: "SAP S/4 HANA",
  workday: "Workday",
  jde: "JD Edwards",
  custom: "Custom",
};

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
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
              {project.source_system && (
                <span className="inline-flex items-center gap-1 text-brand-dark">
                  <Database className="h-3 w-3" />
                  {SOURCE_DISPLAY[project.source_system] || project.source_system}
                </span>
              )}
              {project.target_environment && (
                <span className="truncate">→ {project.target_environment}</span>
              )}
            </div>
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

// ─────── New Engagement page — Setup Wizard ───────
//
// Lives at the same /projects/new route the simple form occupied before;
// the page wraps the four-step SetupWizard so the route count is unchanged
// while the UX picks up Source System + Connection in the same flow.

export const NewProjectPage: React.FC = () => (
  <>
    <PageTitle
      title="New Engagement"
      subtitle="Setup Wizard — engagement details, source system, source connection."
      right={
        <Link to="/projects" className="btn-ghost">
          <ArrowLeft className="h-4 w-4" /> Back
        </Link>
      }
    />
    <SetupWizard />
  </>
);
