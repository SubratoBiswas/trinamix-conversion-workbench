import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactFlow, {
  Background, Controls, MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  ArrowLeft, Plus, Building2, Calendar, Network, Layers,
  Database, FileSpreadsheet, AlertCircle, CheckCircle2, Clock,
  PlayCircle, ArrowRight, Activity, CheckCircle, CircleDot,
} from "lucide-react";
import { DependencyApi, ProjectsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { CopilotFab } from "@/components/copilot/CopilotFab";
import { CutoverPanel } from "@/components/cutover/CutoverPanel";
import { ExecSummaryCard } from "@/components/cutover/ExecSummaryCard";
import { DiscoveryPanel } from "@/components/discovery/DiscoveryPanel";
import { SourceConnectionCard } from "@/components/source/SourceConnectionCard";
import { cn, formatDate } from "@/lib/utils";
import type { Conversion, Dependency, Project } from "@/types";

// Source-system code → display label for the engagement subtitle. The full
// catalog is server-driven via /api/source-systems; this is the static
// fallback used in the subtitle line.
const SOURCE_DISPLAY: Record<string, string> = {
  netsuite: "NetSuite",
  oracle_ebs: "Oracle EBS",
  sap_ecc: "SAP ECC",
  sap_s4: "SAP S/4 HANA",
  workday: "Workday",
  jde: "JD Edwards",
  custom: "Custom",
};

const PHASES: { code: string; label: string; detail: string }[] = [
  { code: "blueprint", label: "Blueprint", detail: "Discovery + scoping + design sign-off" },
  { code: "own",       label: "Own",       detail: "Build + SIT (mapping, transforms, validation)" },
  { code: "lift",      label: "Lift",      detail: "Load (DEV / QA / UAT → cutover)" },
  { code: "thrive",    label: "Thrive",    detail: "Stabilisation + hypercare" },
];

const STATUS_TONE = (s: string) => {
  if (s === "loaded" || s === "complete") return "success";
  if (s === "failed") return "danger";
  if (s === "planning") return "info";
  if (s === "on_hold") return "neutral";
  return "warning";
};

/**
 * Engagement detail page.
 *
 * Shows the engagement metadata, all conversion objects within it, and a
 * project-scoped dependency graph that plots each conversion as a node and
 * draws arrows between them based on the global object-type dependency map.
 */
export const ProjectOverviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);

  const [project, setProject] = useState<Project | null>(null);
  const [conversions, setConversions] = useState<Conversion[] | null>(null);
  const [deps, setDeps] = useState<Dependency[]>([]);

  useEffect(() => {
    ProjectsApi.get(pid).then(setProject);
    ProjectsApi.conversions(pid).then(setConversions);
    DependencyApi.list().then(setDeps);
  }, [pid]);

  if (!project || !conversions) return <PageLoader />;

  const totals = {
    total: conversions.length,
    planning: conversions.filter(c => c.status === "planning").length,
    inProgress: conversions.filter(c =>
      ["draft", "mapping_suggested", "awaiting_approval", "validated", "output_generated"].includes(c.status)
    ).length,
    loaded: conversions.filter(c => c.status === "loaded").length,
    failed: conversions.filter(c => c.status === "failed").length,
  };
  const pct = totals.total > 0 ? Math.round((totals.loaded / totals.total) * 100) : 0;

  return (
    <>
      <PageTitle
        title={project.name}
        subtitle={
          <span className="flex items-center gap-3 text-[12.5px]">
            <span className="inline-flex items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5" /> {project.client || "—"}
            </span>
            {project.target_environment && (
              <>
                <span>→</span>
                <span>{project.target_environment}</span>
              </>
            )}
            {project.source_system && (
              <span className="inline-flex items-center gap-1.5 text-brand-dark">
                <Database className="h-3.5 w-3.5" />
                {SOURCE_DISPLAY[project.source_system] || project.source_system}
              </span>
            )}
            {project.go_live_date && (
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5" /> Go-live {formatDate(project.go_live_date)}
              </span>
            )}
            <Pill tone={STATUS_TONE(project.status)}>{project.status.replace("_", " ")}</Pill>
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Link to="/projects" className="btn-ghost">
              <ArrowLeft className="h-4 w-4" /> All engagements
            </Link>
            <Link to={`/projects/${pid}/cutover`} className="btn-ghost">
              <Activity className="h-4 w-4" /> Migration Monitor
            </Link>
            <Button variant="primary">
              <Plus className="h-4 w-4" /> Add Conversion
            </Button>
          </div>
        }
      />

      {/* Phase bar — lifecycle position of this engagement */}
      <PhaseBar project={project} onPhaseChange={async (next) => {
        await ProjectsApi.update(project.id, { phase: next });
        ProjectsApi.get(project.id).then(setProject);
      }} />

      {/* Top KPI strip */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <KpiTile label="Conversions" value={totals.total} icon={<Layers className="h-3.5 w-3.5" />} tone="text-ink" />
        <KpiTile label="Planning"    value={totals.planning} icon={<Clock className="h-3.5 w-3.5" />} tone="text-info" />
        <KpiTile label="In progress" value={totals.inProgress} icon={<PlayCircle className="h-3.5 w-3.5" />} tone="text-warning" />
        <KpiTile label="Loaded"      value={totals.loaded} icon={<CheckCircle2 className="h-3.5 w-3.5" />} tone="text-success" />
        <KpiTile label="Failed"      value={totals.failed} icon={<AlertCircle className="h-3.5 w-3.5" />} tone="text-danger" />
      </div>

      {/* Progress bar */}
      <Card className="mt-4">
        <CardBody>
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-ink">Engagement progress</span>
            <span className="font-mono tabular-nums text-ink">{totals.loaded} / {totals.total} loaded · {pct}%</span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-success transition-all" style={{ width: `${pct}%` }} />
          </div>
        </CardBody>
      </Card>

      {/* Exec summary — CFO / steering committee rollup */}
      <ExecSummaryCard projectId={project.id} />

      {/* Discovery — 6-pillar inventory + Integration Health table */}
      <DiscoveryPanel
        projectId={project.id}
        hasConnection={Boolean(project.has_active_source_connection || (project.source_connection_count || 0) > 0)}
      />

      {/* Cutover orchestration — Safeguards / Runbook / Recon / Issues / Risks / Rehearsals / Sign-offs */}
      <CutoverPanel projectId={project.id} />

      {/* Two-column layout */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Conversions list */}
        <Card className="lg:col-span-2">
          <CardHeader title="Conversion Objects" subtitle={`${totals.total} object${totals.total === 1 ? "" : "s"} ordered by planned load sequence`} />
          {conversions.length === 0 ? (
            <CardBody>
              <EmptyState
                icon={<Layers className="h-5 w-5" />}
                title="No conversion objects yet"
                description="Add the first conversion object to this engagement (e.g. Item Master, Customer Master)."
              />
            </CardBody>
          ) : (
            <table className="table-shell">
              <thead>
                <tr>
                  <th className="!w-12 text-right">#</th>
                  <th>Object</th>
                  <th>Target</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {conversions.map((c, idx) => (
                  <tr key={c.id}>
                    <td className="text-right font-mono text-[11px] text-ink-subtle">{idx + 1}</td>
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
                        <span className="inline-flex items-center gap-1 text-[12px] text-ink">
                          <FileSpreadsheet className="h-3 w-3 text-indigo-500" />
                          {c.template_name}
                        </span>
                      ) : <span className="text-ink-subtle italic">not selected</span>}
                    </td>
                    <td>
                      {c.dataset_name ? (
                        <span className="inline-flex items-center gap-1 text-[12px] text-ink">
                          <Database className="h-3 w-3 text-emerald-500" />
                          {c.dataset_name}
                        </span>
                      ) : <span className="text-ink-subtle italic">awaiting file</span>}
                    </td>
                    <td><Pill tone={STATUS_TONE(c.status)}>{c.status.replace("_", " ")}</Pill></td>
                    <td className="text-right">
                      <Link to={`/conversions/${c.id}`} className="btn-ghost h-7 px-2 text-xs">
                        Open <ArrowRight className="h-3 w-3" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {/* Right column: Source Connection + Load Order graph */}
        <div className="flex flex-col gap-4">
          <SourceConnectionCard
            projectId={project.id}
            projectSourceSystem={project.source_system}
          />
          <Card>
            <CardHeader
              title={<><Network className="mr-2 inline h-4 w-4 text-brand" />Load Order</>}
              subtitle="Conversion objects + cross-object dependencies"
            />
            <div className="h-[400px]">
              <ProjectDependencyGraph conversions={conversions} dependencies={deps} />
            </div>
          </Card>
        </div>
      </div>

      {project.description && (
        <Card className="mt-4">
          <CardHeader title="Notes" />
          <CardBody>
            <p className="whitespace-pre-wrap text-sm text-ink">{project.description}</p>
          </CardBody>
        </Card>
      )}

      {/* Floating Copilot — only renders when ANTHROPIC_API_KEY is configured server-side. */}
      <CopilotFab projectId={project.id} />
    </>
  );
};

const KpiTile: React.FC<{ label: string; value: number; icon: React.ReactNode; tone: string }> = ({ label, value, icon, tone }) => (
  <div className="card p-3">
    <div className={cn("flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted", tone)}>
      {icon}{label}
    </div>
    <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
  </div>
);

// ─────── Project-scoped dependency graph ───────

const ProjectDependencyGraph: React.FC<{
  conversions: Conversion[];
  dependencies: Dependency[];
}> = ({ conversions, dependencies }) => {
  const { nodes, edges } = useMemo(() => {
    // Map target_object → conversion (the engagement's actual objects)
    const byObject: Record<string, Conversion> = {};
    for (const c of conversions) {
      if (c.target_object) byObject[c.target_object.toLowerCase()] = c;
    }

    // Compute depth for layered layout
    const incoming = new Map<string, string[]>();
    const outgoing = new Map<string, string[]>();
    const objectsInProject = new Set<string>(Object.keys(byObject));
    for (const d of dependencies) {
      const s = d.source_object.toLowerCase(), t = d.target_object.toLowerCase();
      if (!objectsInProject.has(s) || !objectsInProject.has(t)) continue;
      incoming.set(t, [...(incoming.get(t) || []), s]);
      outgoing.set(s, [...(outgoing.get(s) || []), t]);
    }
    const depth = new Map<string, number>();
    const roots = Array.from(objectsInProject).filter(o => !(incoming.get(o)?.length));
    const q: [string, number][] = roots.map(r => [r, 0]);
    while (q.length) {
      const [n, d] = q.shift()!;
      if ((depth.get(n) || -1) >= d) continue;
      depth.set(n, d);
      for (const nx of outgoing.get(n) || []) q.push([nx, d + 1]);
    }

    // Layout
    const COL_W = 200, ROW_H = 80, OFF_X = 30, OFF_Y = 30;
    const byDepth = new Map<number, string[]>();
    for (const o of objectsInProject) {
      const d = depth.get(o) ?? 0;
      byDepth.set(d, [...(byDepth.get(d) || []), o]);
    }

    const ns: any[] = [];
    Array.from(byDepth.entries()).sort((a, b) => a[0] - b[0]).forEach(([d, list]) => {
      list.forEach((obj, i) => {
        const c = byObject[obj];
        if (!c) return;
        const tone = c.status === "loaded" ? "ok" :
                     c.status === "failed" ? "failed" :
                     c.status === "planning" ? "planned" : "active";
        const colors = {
          ok:      { bg: "#FFFFFF", border: "#10B981", text: "#0F172A" },
          failed:  { bg: "#FEF2F2", border: "#EF4444", text: "#7F1D1D" },
          planned: { bg: "#F8FAFC", border: "#94A3B8", text: "#475569" },
          active:  { bg: "#FFFBEB", border: "#F59E0B", text: "#78350F" },
        }[tone];
        ns.push({
          id: c.target_object || c.name,
          position: { x: d * COL_W + OFF_X, y: i * ROW_H + OFF_Y },
          data: { label: c.target_object || c.name },
          style: {
            width: 160,
            background: colors.bg,
            border: `2px solid ${colors.border}`,
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11,
            fontWeight: 500,
            color: colors.text,
          },
        });
      });
    });

    const es: any[] = dependencies
      .filter(d =>
        objectsInProject.has(d.source_object.toLowerCase()) &&
        objectsInProject.has(d.target_object.toLowerCase())
      )
      .map((d, i) => ({
        id: `e${i}`,
        source: d.source_object,
        target: d.target_object,
        type: "smoothstep",
        style: { stroke: "#94A3B8", strokeWidth: 1.25 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
      }));

    return { nodes: ns, edges: es };
  }, [conversions, dependencies]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs text-ink-muted">
        Add conversions with target objects to see the project-scoped dependency map.
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes} edges={edges}
      fitView fitViewOptions={{ padding: 0.2 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable nodesConnectable={false}
      minZoom={0.4} maxZoom={1.5}
    >
      <Background color="#E2E8F0" gap={18} />
      <Controls className="!shadow-card" showInteractive={false} />
    </ReactFlow>
  );
};

// ─────── Phase bar — engagement lifecycle ───────

const PhaseBar: React.FC<{
  project: Project;
  onPhaseChange: (next: string) => Promise<void> | void;
}> = ({ project, onPhaseChange }) => {
  const current = project.phase || "blueprint";
  const currentIdx = Math.max(0, PHASES.findIndex((p) => p.code === current));
  const [busy, setBusy] = useState<string | null>(null);

  const advance = async (code: string) => {
    if (code === current || busy) return;
    setBusy(code);
    try {
      await onPhaseChange(code);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mb-4 rounded-lg border border-line bg-white p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          Lifecycle phase
        </span>
        <span className="text-[11px] text-ink-muted">{PHASES[currentIdx]?.detail}</span>
      </div>
      <ol className="flex items-center gap-2">
        {PHASES.map((p, i) => {
          const done = i < currentIdx;
          const active = i === currentIdx;
          return (
            <React.Fragment key={p.code}>
              <li className="flex-1">
                <button
                  onClick={() => advance(p.code)}
                  disabled={busy !== null}
                  className={cn(
                    "group w-full rounded-md border px-3 py-2 text-left transition",
                    active && "border-brand bg-brand-subtle/40",
                    done && "border-success bg-success-subtle/40",
                    !active && !done && "border-line bg-white hover:border-brand-dark/30",
                  )}
                  title={`Switch to ${p.label}`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className={cn(
                      "flex h-5 w-5 items-center justify-center rounded-full text-[10.5px] font-semibold",
                      active && "bg-brand text-white",
                      done && "bg-success text-white",
                      !active && !done && "bg-canvas text-ink-muted",
                    )}>
                      {done ? <CheckCircle className="h-3 w-3" /> :
                       active ? <CircleDot className="h-3 w-3" /> :
                       <span>{i + 1}</span>}
                    </span>
                    <span className={cn(
                      "text-xs font-semibold",
                      active && "text-brand-dark",
                      done && "text-success",
                      !active && !done && "text-ink-muted",
                    )}>
                      {p.label}
                    </span>
                  </div>
                </button>
              </li>
              {i < PHASES.length - 1 && (
                <span className={cn(
                  "h-px w-6 shrink-0",
                  done ? "bg-success" : "bg-line",
                )} />
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </div>
  );
};
