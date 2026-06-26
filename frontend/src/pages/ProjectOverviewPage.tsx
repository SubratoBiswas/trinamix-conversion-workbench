import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactFlow, {
  Background, Controls, MarkerType, useEdgesState, useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  ArrowLeft, Plus, Building2, Calendar, Network, Layers,
  Database, FileSpreadsheet, AlertCircle, CheckCircle2, Clock,
  PlayCircle, ArrowRight, Activity, Wand2, GitBranch, RefreshCw, Zap,
} from "lucide-react";
import { ConversionsApi, DatasetsApi, DependencyApi, FbdiApi, FusionModulesApi, ProjectsApi } from "@/api";
import { api } from "@/api/client";
import type { Dataset, FBDITemplate, FusionModule, ScopeHints } from "@/types";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate, statusTone } from "@/lib/utils";
import type { Conversion, Dependency, Project } from "@/types";
import { ExecSummaryCard } from "@/components/cutover/ExecSummaryCard";
import { CutoverPanel } from "@/components/cutover/CutoverPanel";
import { SourceConnectionCard } from "@/components/source/SourceConnectionCard";
import { DiscoveryPanel } from "@/components/discovery/DiscoveryPanel";

// ─── Lifecycle phases ────────────────────────────────────────────────────────

const PHASES: { code: string; label: string }[] = [
  { code: "blueprint", label: "Blueprint" },
  { code: "own",       label: "Own" },
  { code: "lift",      label: "Lift" },
  { code: "thrive",    label: "Thrive" },
];

const LifecycleTracker: React.FC<{ phase: string | null | undefined }> = ({ phase }) => {
  const current = PHASES.findIndex(p => p.code === (phase ?? "blueprint"));
  return (
    <Card className="mb-4">
      <CardBody className="!py-3">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
          Lifecycle Phase
        </div>
        <div className="flex items-center gap-0">
          {PHASES.map((p, i) => {
            const done    = i < current;
            const active  = i === current;
            const future  = i > current;
            return (
              <React.Fragment key={p.code}>
                <div
                  className={cn(
                    "flex min-w-[140px] flex-1 items-center gap-2 rounded-lg border-2 px-3 py-2 text-sm font-medium transition-all",
                    done   ? "border-success/60 bg-success/5 text-success"      :
                    active ? "border-brand/70 bg-brand-subtle text-brand-dark"  :
                             "border-line bg-canvas text-ink-muted",
                  )}
                >
                  {done ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                  ) : active ? (
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-brand bg-brand">
                      <span className="h-1.5 w-1.5 rounded-full bg-white" />
                    </span>
                  ) : (
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-line bg-canvas text-[10px] font-bold text-ink-muted">
                      {i + 1}
                    </span>
                  )}
                  {p.label}
                </div>
                {i < PHASES.length - 1 && (
                  <div className={cn(
                    "h-0.5 w-6 shrink-0",
                    i < current ? "bg-success" : "bg-line",
                  )} />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
};

// ─── Status tone helpers ─────────────────────────────────────────────────────

const STATUS_TONE = (s: string) => {
  if (s === "loaded" || s === "complete") return "success";
  if (s === "failed") return "danger";
  if (s === "planning") return "info";
  if (s === "on_hold") return "neutral";
  return "warning";
};

// ─── Page ────────────────────────────────────────────────────────────────────

export const ProjectOverviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const pid = id!;

  const [project, setProject] = useState<Project | null>(null);
  const [conversions, setConversions] = useState<Conversion[] | null>(null);
  const [deps, setDeps] = useState<Dependency[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [ebsBusy, setEbsBusy] = useState(false);
  const [showModuleModal, setShowModuleModal] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [hasConnection, setHasConnection] = useState(false);

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3000); };

  const refresh = () => {
    ProjectsApi.get(pid).then(setProject);
    ProjectsApi.conversions(pid).then(setConversions);
    DependencyApi.list().then(setDeps);
  };

  const autoPopulateIfNeeded = async (proj: typeof project, convs: typeof conversions) => {
    if (!proj || !convs) return;
    if (convs.length > 0) return;
    const mods = (proj as any).selected_modules as string[] | undefined;
    if (!mods || mods.length === 0) return;
    try {
      const r = await ProjectsApi.autoPopulate(proj.id, mods);
      flash(`Auto-populated ${r.created?.length ?? 0} conversion(s) from saved modules`);
      refresh();
    } catch { /* silent */ }
  };

  useEffect(() => {
    Promise.all([
      ProjectsApi.get(pid),
      ProjectsApi.conversions(pid),
      DependencyApi.list().then(setDeps),
      api.get(`/projects/${pid}/source-connections`).then(r => r.data).catch(() => []),
    ]).then(([proj, convs, , conns]) => {
      setProject(proj);
      setConversions(convs);
      setHasConnection(Array.isArray(conns) && conns.length > 0);
      autoPopulateIfNeeded(proj, convs);
    });
  }, [pid]);

  if (!project || !conversions) return <PageLoader />;

  const totals = {
    total:      conversions.length,
    planning:   conversions.filter(c => c.status === "planning").length,
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
              <><span>→</span><span>{project.target_environment}</span></>
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
            <Button
              variant="secondary"
              loading={busy === "load_order"}
              onClick={async () => {
                setBusy("load_order");
                try {
                  const r = await ProjectsApi.deriveLoadOrder(pid);
                  flash(`Load order derived for ${r.load_order.length} object(s)`);
                  refresh();
                } finally { setBusy(null); }
              }}
            >
              <GitBranch className="h-4 w-4" /> Derive Load Order
            </Button>
            <Button
              variant="secondary"
              onClick={() => setShowModuleModal(true)}
            >
              <Wand2 className="h-4 w-4" /> Auto-populate
            </Button>
            <Button variant="primary" onClick={() => setShowAddModal(true)}>
              <Plus className="h-4 w-4" /> Add Conversion
            </Button>
          </div>
        }
      />

      {/* Lifecycle phase tracker */}
      <LifecycleTracker phase={(project as any).phase} />

      {/* KPI strip */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <KpiTile label="Conversions" value={totals.total}      icon={<Layers      className="h-3.5 w-3.5" />} tone="text-ink" />
        <KpiTile label="Planning"    value={totals.planning}   icon={<Clock       className="h-3.5 w-3.5" />} tone="text-info" />
        <KpiTile label="In progress" value={totals.inProgress} icon={<PlayCircle  className="h-3.5 w-3.5" />} tone="text-warning" />
        <KpiTile label="Loaded"      value={totals.loaded}     icon={<CheckCircle2 className="h-3.5 w-3.5" />} tone="text-success" />
        <KpiTile label="Failed"      value={totals.failed}     icon={<AlertCircle className="h-3.5 w-3.5" />} tone="text-danger" />
      </div>

      {/* Progress bar */}
      <Card className="mb-4">
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

      {/* Migration Readiness exec summary */}
      <ExecSummaryCard projectId={pid} />

      {/* Cutover Orchestration */}
      <CutoverPanel projectId={pid} />

      {/* Conversion Objects + Source Connection + Load Order */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Conversion Objects"
            subtitle={`${totals.total} object${totals.total === 1 ? "" : "s"} ordered by planned load sequence`}
            actions={
              <button
                onClick={async () => {
                  setEbsBusy(true);
                  try {
                    const res = await ConversionsApi.switchProjectToEbs(pid);
                    setToast(res.message);
                    const updated = await ConversionsApi.list({ project_id: pid });
                    setConversions(updated);
                  } catch (e: any) {
                    setToast(`Failed: ${e?.response?.data?.detail || e?.message}`);
                  } finally { setEbsBusy(false); }
                }}
                disabled={ebsBusy}
                className="inline-flex items-center gap-1.5 rounded-md border border-emerald-300 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
                title="Remove uploaded datasets and use live Oracle EBS as source for all conversions"
              >
                <Zap className="h-3 w-3" />
                {ebsBusy ? "Switching…" : "Use EBS Source"}
              </button>
            }
          />
          {conversions.length === 0 ? (
            <CardBody>
              <EmptyState
                icon={<Layers className="h-5 w-5" />}
                title="No conversion objects yet"
                description="Add the first conversion object to this engagement."
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
                      {c.template_name
                        ? <span className="inline-flex items-center gap-1 text-[12px] text-ink"><FileSpreadsheet className="h-3 w-3 text-indigo-500" />{c.template_name}</span>
                        : <span className="text-ink-subtle italic">not selected</span>}
                    </td>
                    <td>
                      {!c.dataset_id
                        ? <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                            <Zap className="h-3 w-3" />
                            {c.ebs_table_hint ? c.ebs_table_hint : "EBS Live"}
                          </span>
                        : c.dataset_name
                          ? <span className="inline-flex items-center gap-1 text-[12px] text-ink"><Database className="h-3 w-3 text-emerald-500" />{c.dataset_name}</span>
                          : <span className="text-ink-subtle italic">awaiting file</span>}
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

        {/* Right sidebar: Source Connection + Load Order */}
        <div className="flex flex-col gap-4">
          <SourceConnectionCard
            projectId={pid}
            projectSourceSystem={(project as any).source_system}
          />
          <Card>
            <CardHeader
              title={<><Network className="mr-2 inline h-4 w-4 text-brand" />Load Order</>}
              subtitle="Conversion objects + cross-object dependencies"
            />
            <div className="h-[320px]">
              <ProjectDependencyGraph conversions={conversions} dependencies={deps} />
            </div>
          </Card>
        </div>
      </div>

      {/* Discovery */}
      <div className="mt-4">
        <DiscoveryPanel projectId={pid} hasConnection={hasConnection} />
      </div>

      {/* Source coverage — only rendered when a discovery scan exists */}
      <div className="mt-4">
        <ScopeHintsCard
          projectId={pid}
          sourceSystem={(project as any).source_system || "oracle_ebs"}
          selectedModules={(project as any).selected_modules || []}
        />
      </div>

      {/* Notes */}
      {project.description && (
        <Card className="mt-4">
          <CardHeader title="Notes" />
          <CardBody>
            <p className="whitespace-pre-wrap text-sm text-ink">{project.description}</p>
          </CardBody>
        </Card>
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-50 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}

      {showAddModal && (
        <AddConversionModal
          projectId={pid}
          onClose={() => setShowAddModal(false)}
          onDone={() => { flash("Conversion created"); refresh(); setShowAddModal(false); }}
        />
      )}

      {showModuleModal && (
        <AutoPopulateModal
          projectId={pid}
          onClose={() => setShowModuleModal(false)}
          onDone={(r) => { flash(`Created ${r.created_count} conversion(s)`); refresh(); setShowModuleModal(false); }}
        />
      )}
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

// ─────── Project-scoped dependency graph ───────────────────────────────────

const ProjectDependencyGraph: React.FC<{
  conversions: Conversion[];
  dependencies: Dependency[];
}> = ({ conversions, dependencies }) => {
  const { nodes, edges } = useMemo(() => {
    const byObject: Record<string, Conversion> = {};
    for (const c of conversions) {
      if (c.target_object) byObject[c.target_object.toLowerCase()] = c;
    }

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
        Add conversions with target objects to see the dependency map.
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

// ─────── Add Conversion Modal ───────────────────────────────────────────────

const OBJECT_TYPES = [
  "Supplier", "Item Master", "Customer", "Purchase Order", "Sales Order",
  "Open AP Invoice", "Open AR Invoice", "GL Journal", "Asset", "Employee",
  "Bank Account", "Cost Center", "Chart of Accounts", "BOM", "Work Order",
];

const AddConversionModal: React.FC<{
  projectId: string;
  onClose: () => void;
  onDone: () => void;
}> = ({ projectId, onClose, onDone }) => {
  const [name, setName] = React.useState("");
  const [targetObject, setTargetObject] = React.useState("");
  const [datasetId, setDatasetId] = React.useState("");
  const [templateId, setTemplateId] = React.useState("");
  const [datasets, setDatasets] = React.useState<Dataset[]>([]);
  const [templates, setTemplates] = React.useState<FBDITemplate[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    DatasetsApi.list().then(setDatasets).catch(() => {});
    FbdiApi.list().then(setTemplates).catch(() => {});
  }, []);

  const submit = async () => {
    if (!name.trim()) { setErr("Name is required"); return; }
    setBusy(true);
    setErr(null);
    try {
      const conv = await ConversionsApi.create({
        project_id: projectId,
        name: name.trim(),
        target_object: targetObject.trim() || undefined,
      } as any);
      if ((datasetId || templateId) && conv.id) {
        await ConversionsApi.update(conv.id, {
          ...(datasetId ? { dataset_id: datasetId } : {}),
          ...(templateId ? { template_id: templateId } : {}),
        } as any);
      }
      onDone();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Failed to create conversion");
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-ink">Add Conversion Object</h2>
            <p className="mt-0.5 text-xs text-ink-muted">Create a new conversion object in this engagement.</p>
          </div>
          <button onClick={onClose} className="text-ink-subtle hover:text-ink text-lg leading-none">&times;</button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-ink mb-1">Name <span className="text-danger">*</span></label>
            <input
              autoFocus value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Purchase Order"
              className="w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none"
              onKeyDown={e => e.key === "Enter" && submit()}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-ink mb-1">Target Object Type</label>            <select value={targetObject} onChange={e => setTargetObject(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none">
              <option value="">-- select or leave blank --</option>
              {OBJECT_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-ink mb-1">Source Dataset</label>
            <select value={datasetId} onChange={e => setDatasetId(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none">
              <option value="">-- select or upload later --</option>
              {datasets.map(d => <option key={d.id} value={d.id}>{d.name} ({d.row_count ?? 0} rows)</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-ink mb-1">Target FBDI Template</label>
            <select value={templateId} onChange={e => setTemplateId(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none">
              <option value="">-- select or bind later --</option>
              {templates.map(t => <option key={t.id} value={t.id}>{t.name}{t.business_object ? " - " + t.business_object : ""}</option>)}
            </select>
          </div>
          {err && <p className="text-xs text-danger">{err}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="btn-ghost">Cancel</button>
          <button onClick={submit} disabled={!name.trim() || busy} className="btn-primary disabled:opacity-50">
            {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {busy ? "Creating..." : "Create Conversion"}
          </button>
        </div>
      </div>
    </div>
  );
};

// ─────── Source Coverage (Scope Hints) Card ────────────────────────────────

/** Extract the first ALL_CAPS table name from a source-extract hint string.
 *  "Extract from MTL_SYSTEM_ITEMS_B"   → "MTL_SYSTEM_ITEMS_B"
 *  "Extract from FA_BOOKS / FA_ASSET_HISTORY" → "FA_BOOKS"
 *  "Saved Search → ..."               → null  */
function extractScopeTable(hint: string): string | null {
  const m = hint.match(/\b([A-Z][A-Z0-9_]{3,})\b/);
  return m ? m[1] : null;
}

const ScopeHintsCard: React.FC<{
  projectId: string;
  sourceSystem: string;
  selectedModules: string[];
}> = ({ projectId, sourceSystem, selectedModules }) => {
  const [hints, setHints] = React.useState<ScopeHints | null>(null);
  const [modules, setModules] = React.useState<FusionModule[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    Promise.all([
      api.get(`/projects/${projectId}/discovery/scope-hints`).then(r => r.data as ScopeHints),
      FusionModulesApi.list(),
    ]).then(([h, mods]) => {
      setHints(h);
      setModules(mods);
    }).catch(() => {}).finally(() => setLoading(false));
  }, [projectId]);

  // Only render if a scan has run and there are selected modules
  if (loading || !hints?.run_id || selectedModules.length === 0) return null;

  // Flatten all canonical objects across selected modules (de-duped)
  const seen = new Set<string>();
  const rows: { label: string; hint: string; table: string | null; found: boolean; rowCount: number | null }[] = [];
  for (const mod of modules) {
    if (!selectedModules.includes(mod.code)) continue;
    for (const obj of mod.objects) {
      if (seen.has(obj.target_object)) continue;
      seen.add(obj.target_object);
      const hint = obj.source_extracts[sourceSystem] || "—";
      const table = hint !== "—" ? extractScopeTable(hint) : null;
      const rowCount = table ? (hints.table_counts[table] ?? null) : null;
      rows.push({
        label: obj.label,
        hint,
        table,
        found: table !== null && table in hints.table_counts,
        rowCount,
      });
    }
  }

  if (rows.length === 0) return null;

  const foundCount = rows.filter(r => r.found).length;
  const isMock = hints.is_mock;

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Database className="h-4 w-4 text-brand" />
            Source Coverage
            {isMock && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                mock scan
              </span>
            )}
          </span>
        }
        subtitle={`${foundCount} of ${rows.length} canonical source tables confirmed in last ${isMock ? "mock" : "live"} scan`}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr className="border-b border-line bg-canvas text-left text-[10px] uppercase tracking-wider text-ink-muted">
              <th className="px-4 py-2">Fusion Object</th>
              <th className="px-4 py-2">Source Table</th>
              <th className="px-4 py-2 text-center">Found</th>
              <th className="px-4 py-2 text-right">Rows in DB</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-line/50 hover:bg-canvas/50">
                <td className="px-4 py-1.5 font-medium text-ink">{r.label}</td>
                <td className="px-4 py-1.5 font-mono text-[10.5px] text-ink-muted">
                  {r.table ?? <span className="italic">—</span>}
                </td>
                <td className="px-4 py-1.5 text-center">
                  {r.table === null ? (
                    <span className="text-ink-muted">—</span>
                  ) : r.found ? (
                    <CheckCircle2 className="inline h-3.5 w-3.5 text-success" />
                  ) : (
                    <AlertCircle className="inline h-3.5 w-3.5 text-danger" />
                  )}
                </td>
                <td className="px-4 py-1.5 text-right font-mono">
                  {r.rowCount !== null ? (
                    <span className={isMock ? "text-amber-700" : "text-ink"}>
                      {r.rowCount.toLocaleString()}
                    </span>
                  ) : r.found ? (
                    <span className="text-[10px] italic text-ink-muted">n/a</span>
                  ) : (
                    <span className="text-[10px] italic text-ink-muted">not found</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ─── Auto-populate Modal ─────────────────────────────────────────────────────
// Uses the real Fusion module CATALOG (codes like "scm") so the backend resolves
// the full canonical object set (e.g. 17 for Supply Chain) — the old hardcoded
// legacy codes ("SCM") only mapped to a 6-object legacy table.

const AutoPopulateModal: React.FC<{
  projectId: string;
  onClose: () => void;
  onDone: (r: any) => void;
}> = ({ projectId, onClose, onDone }) => {
  const [modules, setModules] = React.useState<FusionModule[]>([]);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => { FusionModulesApi.list().then(setModules).catch(() => {}); }, []);

  const toggle = (code: string) =>
    setSelected(prev => prev.includes(code) ? prev.filter(x => x !== code) : [...prev, code]);

  // Preview: unique canonical objects across the selected modules.
  const objCount = React.useMemo(() => {
    const seen = new Set<string>();
    for (const m of modules) {
      if (!selected.includes(m.code)) continue;
      for (const o of (m.objects || [])) seen.add(o.target_object);
    }
    return seen.size;
  }, [modules, selected]);

  const submit = async () => {
    if (!selected.length) return;
    setBusy(true);
    try { const r = await ProjectsApi.autoPopulate(projectId, selected); onDone(r); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-ink">Auto-populate Conversions</h2>
            <p className="mt-0.5 text-xs text-ink-muted">Select Oracle Fusion modules — a conversion is created for every canonical object (existing ones are skipped).</p>
          </div>
          <button onClick={onClose} className="text-ink-subtle hover:text-ink text-lg leading-none">&times;</button>
        </div>
        <div className="mb-4 flex flex-wrap gap-2">
          {modules.map(m => (
            <button key={m.code} onClick={() => toggle(m.code)} title={m.description}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                selected.includes(m.code) ? "border-brand bg-brand-subtle text-brand-dark" : "border-line bg-canvas text-ink-muted hover:border-brand"
              }`}>
              {m.name}{Array.isArray(m.objects) ? ` (${m.objects.length})` : ""}
            </button>
          ))}
        </div>
        {objCount > 0 && (
          <p className="mb-3 text-[11.5px] text-ink-muted">
            {objCount} canonical object{objCount === 1 ? "" : "s"} in scope — any already in this project are skipped.
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="btn-ghost">Cancel</button>
          <button onClick={submit} disabled={!selected.length || busy} className="btn-primary disabled:opacity-50">
            {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {busy ? "Populating..." : `Populate (${objCount} object${objCount !== 1 ? "s" : ""})`}
          </button>
        </div>
      </div>
    </div>
  );
};
