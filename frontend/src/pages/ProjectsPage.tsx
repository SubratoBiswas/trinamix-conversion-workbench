import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Plus, Boxes, Calendar, Building2, ArrowRight, ArrowLeft,
  CheckCircle2, AlertCircle, Clock, Database, Trash2,
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

/** Day only, no clock. The card is being scanned, not read. */
const formatDay = (iso?: string | null) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "2-digit",
    });
  } catch { return iso; }
};

const _created = (p: Project) => {
  const t = new Date(p.created_at ?? 0).getTime();
  return Number.isFinite(t) ? t : 0;
};

/** List of implementation engagements (each contains 30+ conversion objects). */
export const ProjectsPage: React.FC = () => {
  const [items, setItems] = useState<Project[] | null>(null);
  // Oldest-first is the order you want when the job is clearing out old
  // engagements, and it is the one the list has never offered — the API returns
  // newest first, so the ones most likely to go were always at the bottom of 41.
  const [oldestFirst, setOldestFirst] = useState(false);
  useEffect(() => { ProjectsApi.list().then(setItems); }, []);

  const handleDeleted = (id: string | number) =>
    setItems((prev) => (prev ? prev.filter((p) => String(p.id) !== String(id)) : prev));

  const sorted = items === null ? null : [...items].sort(
    (a, b) => (oldestFirst ? _created(a) - _created(b) : _created(b) - _created(a)));

  return (
    <>
      <PageTitle
        title="Projects"
        subtitle="Implementation engagements — each contains many conversion objects"
        right={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setOldestFirst((v) => !v)}
              className="btn-ghost text-[12px]"
              title="Sort by the date the engagement was created"
            >
              <Clock className="h-3.5 w-3.5" />
              {oldestFirst ? "Oldest first" : "Newest first"}
            </button>
            <Link to="/projects/new" className="btn-primary">
              <Plus className="h-4 w-4" /> New Engagement
            </Link>
          </div>
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
            {(sorted ?? items).map((p) => (
              <ProjectCard key={p.id} project={p} onDeleted={handleDeleted} />
            ))}
          </div>
        )
      }
    </>
  );
};

const ProjectCard: React.FC<{ project: Project; onDeleted: (id: string | number) => void }> = ({ project, onDeleted }) => {
  const [deleting, setDeleting] = useState(false);
  const total = project.conversion_count ?? 0;

  const handleDelete = async (e: React.MouseEvent) => {
    e.preventDefault();   // don't follow the card's Link
    e.stopPropagation();
    const n = project.conversion_count ?? 0;
    // Say what goes AND what stays. "This cannot be undone" is true and useless:
    // it does not answer the question anyone actually has, which is whether the
    // uploaded data goes with it. The list below mirrors the cascade in
    // backend/app/routers/projects.py delete_project — keep the two in step.
    if (!window.confirm(
      `Delete engagement "${project.name}"?\n\n` +
      `THIS DELETES\n` +
      `  • the engagement\n` +
      (n ? `  • its ${n} conversion object${n === 1 ? "" : "s"}\n` : "") +
      `  • its datasets and the uploaded files behind them — EXCEPT any also used\n` +
      `    by another engagement, which are kept and listed afterwards\n` +
      `  • its mapping rows, transformation rules and crosswalks\n` +
      `  • generated output records and load run history\n\n` +
      `THIS KEEPS THE MAPPING LOGIC\n` +
      `  • "column A of this source, for this module, maps to column B of the\n` +
      `    FBDI" lives in the learning library, keyed by client and source system\n` +
      `    — not on the conversion. It is captured again just before the delete,\n` +
      `    so a rebuilt engagement picks these mappings straight back up.\n` +
      `  • FBDI templates, gold standards and source connections\n\n` +
      `Cannot be undone.`
    )) return;
    setDeleting(true);
    try {
      const res: any = await ProjectsApi.remove(String(project.id));
      onDeleted(project.id);
      window.dispatchEvent(new Event("workbench:refresh"));  // refresh sidebar counts
      // SAY WHAT WAS SKIPPED. A shared dataset is kept on purpose, and a delete
      // that quietly did less than the dialog promised is the screen and the
      // truth disagreeing again — just in the reassuring direction this time.
      const kept: any[] = res?.datasets_kept ?? [];
      const gone: string[] = res?.datasets_deleted ?? [];
      const errs: string[] = res?.capture_errors ?? [];
      const warns: string[] = res?.warnings ?? [];
      if (kept.length || errs.length || warns.length) {
        alert(
          `Deleted "${project.name}".\n\n` +
          (gone.length ? `Datasets removed: ${gone.join(", ")}\n\n` : "") +
          (kept.length
            ? `Kept ${kept.length} dataset${kept.length === 1 ? "" : "s"} still in use elsewhere:\n` +
              kept.map((k) => `  • ${k.name} — used by ${(k.still_used_by || []).join(", ")}`).join("\n") +
              `\n\nDelete those engagements first if you want these gone too.\n\n`
            : "") +
          // A capture that failed means mapping logic MAY not have reached the
          // library before its rows were deleted. Silence here would be the
          // screen looking right while something was lost.
          (errs.length
            ? `WARNING — the mapping logic could not be captured for:\n` +
              errs.map((e) => `  • ${e}`).join("\n") +
              `\nAnything decided on those conversions and not already learned may not have reached the library.\n\n`
            : "") +
          (warns.length
            ? `The engagement was deleted, but some housekeeping failed:\n` +
              warns.map((w) => `  • ${w}`).join("\n")
            : "")
        );
      }
    } catch (err: any) {
      // SAY WHY. "Failed to delete engagement." is the blank panel that could not
      // explain itself: it cannot tell a timeout from a permission problem from a
      // bad row, so the only next step it leaves anybody is to try again and get
      // the same box. The API's global handler returns a JSON detail — show it.
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        "no further detail was returned";
      const status = err?.response?.status;
      alert(
        `Could not delete "${project.name}".\n\n` +
        (status ? `HTTP ${status}\n` : "") +
        `${detail}\n\n` +
        (status === 403
          ? "This account is not allowed to delete engagements."
          : status === 504 || /timeout|timed out|network/i.test(String(detail))
          ? "The request ran out of time rather than being refused — the engagement may be large. It is safe to try again; the delete is idempotent."
          : "Nothing has been deleted.")
      );
      setDeleting(false);
    }
  };
  const planning = project.planning_count ?? 0;
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
          <div className="flex shrink-0 items-center gap-1.5">
            <Pill tone={STATUS_TONE[project.status] || "neutral"}>{project.status.replace("_", " ")}</Pill>
            <button
              onClick={handleDelete}
              disabled={deleting}
              title="Delete engagement"
              className="rounded p-1 text-ink-subtle hover:bg-danger-subtle hover:text-danger disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
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

        {/* Object roll-ups — mirrors the project detail page's KPI tiles */}
        <div className="mt-3 grid grid-cols-5 gap-1.5 text-center text-[10.5px]">
          <Roll label="Total"       count={total}   icon={<Boxes className="h-3 w-3" />} tone="text-ink" />
          <Roll label="Planning"    count={planning} icon={<Clock className="h-3 w-3" />} tone="text-info" />
          <Roll label="In progress" count={inProg}  icon={<Clock className="h-3 w-3" />} tone="text-warning" />
          <Roll label="Loaded"      count={loaded}  icon={<CheckCircle2 className="h-3 w-3" />} tone="text-success" />
          <Roll label="Failed"      count={failed}  icon={<AlertCircle className="h-3 w-3" />} tone="text-danger" />
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-line bg-canvas px-5 py-2 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-3">
          {/* Created is what tells the old engagements from the current ones —
              with 40+ on screen and most of them named for a test, the name
              alone cannot. Date only: the time of day is noise here. */}
          <span className="inline-flex items-center gap-1" title={formatDate(project.created_at)}>
            <Clock className="h-3 w-3" />
            Created: {formatDay(project.created_at)}
          </span>
          <span className="inline-flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            Go-live: {project.go_live_date ? formatDay(project.go_live_date) : "—"}
          </span>
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
