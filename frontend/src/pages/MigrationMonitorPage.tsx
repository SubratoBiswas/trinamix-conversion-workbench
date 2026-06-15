import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft, Building2, Calendar, ShieldCheck, AlertTriangle,
  CheckCircle2, Loader2, Clock, XCircle, Zap, ArrowRight,
  Plus, Upload,
} from "lucide-react";
import { CutoverApi, ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { PromoteToEnvironmentModal } from "@/components/cutover/PromoteToEnvironmentModal";
import { cn, formatDate } from "@/lib/utils";
import type {
  Conversion, CutoverDashboard, CutoverEnvironmentColumn, CutoverStage, Project,
} from "@/types";

const STATUS_TONE: Record<string, { dotClass: string; pillTone: "success" | "warning" | "danger" | "neutral" | "brand" | "info"; icon: React.ElementType }> = {
  complete: { dotClass: "bg-success",    pillTone: "success", icon: CheckCircle2 },
  running:  { dotClass: "bg-brand",      pillTone: "brand",   icon: Loader2 },
  pending:  { dotClass: "bg-ink-subtle", pillTone: "neutral", icon: Clock },
  failed:   { dotClass: "bg-danger",     pillTone: "danger",  icon: XCircle },
  blocked:  { dotClass: "bg-warning",    pillTone: "warning", icon: AlertTriangle },
};

// Each environment column gets a top accent line in its own colour
const ENV_ACCENT: Record<string, string> = {
  DEV:  "border-info",
  QA:   "border-brand",
  UAT:  "border-warning",
  PROD: "border-danger",
};

const ENV_ACCENT_TEXT: Record<string, string> = {
  DEV:  "text-info",
  QA:   "text-brand-dark",
  UAT:  "text-warning",
  PROD: "text-danger",
};

/**
 * Migration Monitor — the project-level cutover dashboard.
 *
 * Shows days-to-go-live, cutover window, environment ladder (DEV/QA/UAT/PROD)
 * with each environment's stage statuses, SOX notice, and pipeline runs log.
 * Mirrors the layout from the Bolt Migrate / migration-monitor reference.
 */
export const MigrationMonitorPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);

  const [project, setProject] = useState<Project | null>(null);
  const [board, setBoard] = useState<CutoverDashboard | null>(null);
  const [conversions, setConversions] = useState<Conversion[]>([]);
  // P4 — Promote action. `promoteFor` carries the conversion the user clicked.
  const [promoteFor, setPromoteFor] = useState<Conversion | null>(null);
  // Banner shown after a successful promote
  const [flash, setFlash] = useState<string | null>(null);

  const refresh = () => {
    if (!pid) return;
    ProjectsApi.get(pid).then(setProject);
    CutoverApi.dashboard(pid).then(setBoard);
    ProjectsApi.conversions(pid).then(setConversions);
  };

  useEffect(() => { refresh(); }, [pid]);

  // Auto-dismiss the flash so it doesn't linger when the user navigates around
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(t);
  }, [flash]);

  // Look up a Conversion object for a given stage row — needed because the
  // dashboard payload only carries summary fields and the modal needs the
  // full Conversion shape.
  const conversionFor = (cid: number): Conversion | null =>
    conversions.find((c) => c.id === cid) || null;

  if (!project || !board) return <PageLoader />;

  const ENGAGEMENT_BANNER = (
    <div className="mb-3 flex items-center justify-between gap-3 rounded-md border border-line bg-canvas px-3 py-2 text-[12px]">
      <span className="text-ink-muted">
        Engagement <span className="font-semibold text-ink">{project.name}</span>
        {project.client && <> · {project.client}</>}
        {project.source_system && <> · src <span className="font-mono text-ink">{project.source_system}</span></>}
        {Array.isArray(project.selected_modules) && project.selected_modules.length > 0 && (
          <> · scope <span className="text-ink">{project.selected_modules.join(", ")}</span></>
        )}
      </span>
      <Link to="/cutover" className="text-[11.5px] font-medium text-brand-dark hover:underline">
        Switch engagement
      </Link>
    </div>
  );

  const days = board.days_to_go_live;
  const onTrack = (board.environments.find((e) => e.name === "PROD")?.failed_count ?? 0) === 0;

  return (
    <>
      <PageTitle
        title="Migration Monitor"
        subtitle={
          <span className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5" />
              {project.client || project.name}
            </span>
            <span className="text-ink-subtle">·</span>
            <span>{project.target_environment || "Oracle Fusion Cloud"}</span>
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Link to={`/projects/${pid}`} className="btn-ghost">
              <ArrowLeft className="h-4 w-4" /> Engagement overview
            </Link>
            {conversions.length > 0 && (
              <Button
                onClick={() => setPromoteFor(conversions[0])}
                className="!h-8 !text-xs"
                title="Promote a conversion to the next environment"
              >
                <ArrowRight className="h-3.5 w-3.5" /> Promote
              </Button>
            )}
            {days !== null && days !== undefined && (
              <Pill tone={days < 30 ? "danger" : days < 90 ? "warning" : "success"}>
                {days}d to go-live
              </Pill>
            )}
          </div>
        }
      />

      {ENGAGEMENT_BANNER}

      {flash && (
        <div className="mb-3 rounded-md border border-success/40 bg-success-subtle/50 px-3 py-2 text-[12.5px] text-success">
          {flash}
        </div>
      )}

      {/* Days-to-go-live hero card */}
      <Card className="mb-4">
        <CardBody className="!p-0">
          <div className="flex items-stretch">
            <div className="flex w-[260px] shrink-0 flex-col items-center justify-center border-r border-line bg-canvas px-6 py-6 text-center">
              <div className="text-[56px] font-bold leading-none tabular-nums text-brand-dark">
                {days ?? "—"}
              </div>
              <div className="mt-1 text-[10.5px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
                days to go-live
              </div>
            </div>
            <div className="flex-1 px-6 py-5">
              <div className="text-[15px] font-semibold text-ink">Production Cutover Window</div>
              <div className="mt-1 text-[12.5px] text-ink-muted">
                {board.cutover_window_start && board.cutover_window_end ? (
                  <>
                    {formatTimeWindow(board.cutover_window_start, board.cutover_window_end)} UTC
                    <span className="mx-1.5">·</span>
                    All environments staging in parallel
                  </>
                ) : (
                  "Cutover window not yet scheduled."
                )}
              </div>
              <div className="mt-3 flex items-center gap-1.5 text-[12px]">
                {onTrack ? (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                    <span className="font-medium text-success">On track</span>
                  </>
                ) : (
                  <>
                    <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                    <span className="font-medium text-warning">Attention needed</span>
                  </>
                )}
              </div>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* Environments grid */}
      <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-[0.18em] text-ink-muted">
        Environments
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {board.environments.map((env) => (
          <EnvironmentColumn
            key={env.id}
            env={env}
            onPromote={(cid) => {
              const conv = conversionFor(cid);
              if (conv) setPromoteFor(conv);
            }}
          />
        ))}
      </div>

      {/* SOX notice (only when project is sox_controlled) */}
      {board.sox_controlled && (
        <div className="mt-4 rounded-md border border-warning/40 bg-warning-subtle/50 px-4 py-3">
          <div className="flex items-start gap-2">
            <ShieldCheck className="h-4 w-4 shrink-0 text-warning" />
            <div className="text-[12.5px] text-ink">
              <div className="font-semibold text-warning">SOX Notice</div>
              <div className="mt-0.5 text-ink-muted leading-snug">
                Production environment is absent from this monitor view per SOX controls. All production load
                actions require dual sign-off by{" "}
                <code className="rounded bg-white px-1 font-mono text-[11.5px] text-brand-dark">migration_lead</code>{" "}
                and data owner. No auto-remediation permitted. Every stage transition creates an immutable audit trail entry.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Pipeline runs log */}
      <Card className="mt-4">
        <CardHeader
          title="Pipeline Runs"
          subtitle={`${board.pipeline_runs.length} run${board.pipeline_runs.length === 1 ? "" : "s"} logged`}
          actions={
            <Button
              variant="primary" className="!h-8 !px-3 !text-xs"
              disabled={conversions.length === 0}
              onClick={() => conversions.length && setPromoteFor(conversions[0])}
            >
              <Plus className="h-3 w-3" /> New Run
            </Button>
          }
        />
        {board.pipeline_runs.length === 0 ? (
          <CardBody>
            <EmptyState
              icon={<Zap className="h-5 w-5" />}
              title="No pipeline runs yet"
              description="Promote a conversion to QA, UAT, or PROD to start logging environment runs."
            />
          </CardBody>
        ) : (
          <table className="table-shell">
            <thead>
              <tr>
                <th className="!w-20">Run ID</th>
                <th>Entity</th>
                <th>Stage</th>
                <th>Status</th>
                <th className="text-right">Records</th>
                <th>Environment</th>
                <th>Started</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {board.pipeline_runs.map((r) => {
                const tone = STATUS_TONE[r.status] || STATUS_TONE.pending;
                return (
                  <tr key={r.run_id}>
                    <td className="font-mono text-[11.5px] text-ink-muted">#{r.run_id}</td>
                    <td className="font-medium">{r.entity}</td>
                    <td className="text-ink-muted">{r.stage || "—"}</td>
                    <td><Pill tone={tone.pillTone}>{r.status}</Pill></td>
                    <td className="text-right font-mono tabular-nums">{r.records?.toLocaleString() || "—"}</td>
                    <td>{r.environment ? <Pill tone="neutral">{r.environment}</Pill> : "—"}</td>
                    <td className="font-mono text-[11px] text-ink-muted">
                      {r.started ? formatDate(r.started) : "—"}
                    </td>
                    <td className="text-right">
                      <button className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-ink">
                        <ArrowRight className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {/* P4 — Promote-to-environment modal. Shared between header button,
          per-stage Promote actions, and the "New Run" action. */}
      {promoteFor && project && (
        <PromoteToEnvironmentModal
          open
          onClose={() => setPromoteFor(null)}
          conversion={promoteFor}
          project={project}
          onPromoted={(run) => {
            setFlash(`Promoted ${promoteFor.name} → ${run.environment_name}.`);
            setPromoteFor(null);
            refresh();
          }}
        />
      )}
    </>
  );
};

// ─────── Environment column ───────

const EnvironmentColumn: React.FC<{
  env: CutoverEnvironmentColumn;
  onPromote: (conversionId: number) => void;
}> = ({ env, onPromote }) => {
  const total = env.stages.length;
  const accent = ENV_ACCENT[env.name] || "border-line";
  const accentText = ENV_ACCENT_TEXT[env.name] || "text-ink";

  return (
    <div className={cn(
      "flex flex-col rounded-md border-2 bg-white shadow-soft transition hover:shadow-card",
      "border-line"
    )}>
      {/* Header — coloured accent bar at top */}
      <div className={cn("border-t-4 px-4 pb-3 pt-3", accent)}>
        <div className="flex items-center justify-between">
          <div className={cn("text-[13px] font-bold uppercase tracking-[0.12em]", accentText)}>
            {env.name}
          </div>
          {env.sox_controlled && (
            <span title="SOX-controlled" className="inline-flex items-center gap-1 rounded bg-warning-subtle px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-warning">
              <ShieldCheck className="h-2.5 w-2.5" /> SOX
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10.5px] text-ink-muted">
          <span className="tabular-nums">
            {env.complete_count} / {total} complete
          </span>
          {env.failed_count > 0 && (
            <span className="font-mono text-danger">{env.failed_count} failed</span>
          )}
        </div>
      </div>

      {/* Stages list — grouped by track (data / process / integration)
          so the cutover board reflects the full conversion workbench:
          not just data, but also the processes and integrations
          discovered on the engagement. */}
      <div className="flex-1 px-4 pb-4">
        {env.stages.length === 0 ? (
          <div className="py-4 text-center text-[11px] text-ink-subtle">No stages yet.</div>
        ) : (
          <TrackedStages stages={env.stages} envName={env.name} onPromote={onPromote} />
        )}
      </div>
    </div>
  );
};

const TRACK_LABEL: Record<string, string> = {
  data: "Data conversions",
  process: "Processes",
  integration: "Integrations",
};

const TrackedStages: React.FC<{
  stages: CutoverStage[];
  envName: string;
  onPromote: (conversionId: number) => void;
}> = ({ stages, envName, onPromote }) => {
  const groups: Record<string, CutoverStage[]> = {
    data: [],
    process: [],
    integration: [],
  };
  for (const s of stages) {
    const t = s.track || "data";
    (groups[t] ||= []).push(s);
  }
  return (
    <div className="space-y-3">
      {(["data", "process", "integration"] as const).map((track) => {
        const rows = groups[track] || [];
        if (rows.length === 0) return null;
        return (
          <details key={track} open={track === "data"} className="group">
            <summary className="flex cursor-pointer items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              {TRACK_LABEL[track]} <span className="font-mono">{rows.length}</span>
            </summary>
            <div className="mt-1.5 space-y-1.5">
              {rows.map((s, i) => (
                <StageRow
                  key={`${track}-${s.conversion_id ?? s.external_id ?? i}`}
                  stage={s}
                  envName={envName}
                  onPromote={onPromote}
                />
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
};

const StageRow: React.FC<{
  stage: CutoverStage;
  envName: string;
  onPromote: (conversionId: number) => void;
}> = ({ stage, envName, onPromote }) => {
  const tone = STATUS_TONE[stage.status] || STATUS_TONE.pending;
  // Promote action is only meaningful for *data* conversions on non-DEV
  // columns that haven't yet reached `complete`. Process / integration
  // tracks aren't promotable via the EnvironmentRun model; their
  // progression is driven by the runbook + sign-off actions instead.
  const isData = (stage.track || "data") === "data";
  const canPromote = isData && envName !== "DEV" && stage.status !== "complete" && stage.conversion_id != null;
  return (
    <div className="group flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/40 px-2 py-1.5 transition hover:border-brand hover:bg-brand-subtle/30">
      {isData && stage.conversion_id != null ? (
        <Link
          to={`/conversions/${stage.conversion_id}`}
          className="flex min-w-0 flex-1 items-center gap-1.5"
        >
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tone.dotClass)} />
          <span className="truncate text-[12px] font-medium text-ink group-hover:text-brand-dark">
            {stage.conversion_name}
          </span>
        </Link>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-1.5"
             title={stage.target_object || ""}>
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", tone.dotClass)} />
          <span className="truncate text-[12px] font-medium text-ink-muted">
            {stage.conversion_name}
          </span>
        </div>
      )}
      <div className="flex shrink-0 items-center gap-1.5">
        <Pill tone={tone.pillTone} className="!text-[9.5px]">{stage.status}</Pill>
        {canPromote && (
          <button
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onPromote(stage.conversion_id!); }}
            title={`Promote ${stage.conversion_name} to ${envName}`}
            className="rounded p-0.5 text-ink-muted hover:bg-white hover:text-brand-dark"
          >
            <ArrowRight className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
};

// ─────── Helpers ───────

function formatTimeWindow(startIso: string, endIso: string): string {
  try {
    const s = new Date(startIso);
    const e = new Date(endIso);
    const fmt = (d: Date) =>
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    return `${fmt(s)}–${fmt(e)}`;
  } catch {
    return "";
  }
}
