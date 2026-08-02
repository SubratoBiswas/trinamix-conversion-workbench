import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactFlow, {
  Background, Controls, MarkerType, useEdgesState, useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  ArrowLeft, Plus, Building2, Calendar, Network, Layers,
  Database, FileSpreadsheet, AlertCircle, CheckCircle2, Clock,
  PlayCircle, ArrowRight, Activity, Wand2, GitBranch, RefreshCw, Zap, Trash2, Download, FolderDown, Upload,
} from "lucide-react";
import { ConversionsApi, DatasetsApi, DependencyApi, FbdiApi, FusionModulesApi, LearningApi, MappingApi, OutputApi, ProjectsApi } from "@/api";
import type { ReferenceStandard } from "@/api";
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
  // Gold reference standards on file (per object type), loaded from the DB.
  // Once gold has been applied for an object, its standard is stored globally
  // and auto-applied to future conversions — shown here without re-upload.
  const [refStandards, setRefStandards] = useState<ReferenceStandard[]>([]);
  const loadRefStandards = () =>
    LearningApi.referenceStandards().then(setRefStandards).catch(() => setRefStandards([]));
  const refStdFor = (obj?: string | null): ReferenceStandard | undefined => {
    if (!obj) return undefined;
    const k = obj.trim().toLowerCase();
    return refStandards.find((r) => (r.business_object || "").trim().toLowerCase() === k);
  };

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3000); };

  const deleteConversion = async (c: { id: string; name: string }) => {
    if (!window.confirm(`Delete conversion "${c.name}"? This also removes its mappings, rules and outputs. This cannot be undone.`)) return;
    try {
      await ConversionsApi.remove(c.id);
      flash(`Deleted ${c.name}`);
      refresh();
      window.dispatchEvent(new Event("workbench:refresh"));
    } catch {
      flash("Failed to delete conversion");
    }
  };

  // Output delivery type per conversion (manual FBDI download vs live Fusion load).
  const setOutputMode = async (c: Conversion, mode: string) => {
    try { await ConversionsApi.update(String(c.id), { output_mode: mode } as any); refresh(); }
    catch { flash("Couldn't update output type"); }
  };
  const [dl, setDl] = useState<string | null>(null);
  const downloadFbdi = async (c: Conversion) => {
    setDl(String(c.id));
    try {
      await OutputApi.generateAndWait(String(c.id), "csv");
      // download() now honours the server's real filename/extension (supplier is a
      // .zip, not .csv) — the passed name is only a fallback.
      await OutputApi.download(String(c.id), `${(c as any).template_name || c.name}.csv`);
    } catch (e: any) {
      flash(e?.message || "Approve this conversion's mapping first, then download the FBDI file.");
    } finally { setDl(null); }
  };
  // Filled-in Oracle FBDI Excel template (data written into the real template, e.g.
  // the POZ_SUPPLIERS_INT sheet) — generated in the background then downloaded.
  const [dlT, setDlT] = useState<string | null>(null);
  // Agentic plan (checkpoint preview) — read-only draft, nothing runs.
  const [plan, setPlan] = useState<any>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const loadPlan = async () => {
    setPlanLoading(true);
    try { setPlan(await OutputApi.agenticPlan(pid)); }
    catch { setPlan({ objects: [], note: "Couldn't draft a plan right now." }); }
    finally { setPlanLoading(false); }
  };
  const downloadTemplate = async (c: Conversion) => {
    setDlT(String(c.id));
    try {
      await OutputApi.generateAndWait(String(c.id), "template");
      await OutputApi.download(String(c.id), `${(c as any).template_name || c.name}.xlsm`);
    } catch (e: any) {
      flash(e?.message || "Approve this conversion's mapping first, then download the filled template.");
    } finally { setDlT(null); }
  };
  // Generate + download every bound conversion's FBDI file for this engagement
  // as one zip (named/ordered by the supplier load sequence).
  const [dlAll, setDlAll] = useState(false);
  const [dlStatus, setDlStatus] = useState<string | null>(null);
  // Per-object live progress for the Generate-all pipeline, shown as a panel so
  // the user sees exactly what's happening (and what's left) while waiting.
  type GenStage = "queued" | "mapping" | "generating" | "done" | "error";
  const [genProg, setGenProg] = useState<{ key: string; name: string; order: number; stage: GenStage }[]>([]);
  const GEN_STAGE_LABEL: Record<GenStage, string> = {
    queued: "Queued", mapping: "Mapping (deterministic + learnings, AI if needed)",
    generating: "Applying gold + generating", done: "Done", error: "Failed",
  };
  // Full pipeline with live per-object status: AI auto-map every conversion that
  // isn't mapped yet → generate each object's FBDI output → package all into one
  // zip (load-ordered) and download. The client drives each step so it can show
  // exactly what's happening instead of a bare spinner.
  // Header row for the generated bundle. "auto" = the Oracle-correct default:
  // filled Excel templates keep their column labels, CSV/FBDI bundles are
  // headerless because the loader treats a header line as a data row. On/Off
  // force it either way for both buttons.
  const [headerMode, setHeaderMode] = useState<"auto" | "on" | "off">("auto");
  const headerFlag = headerMode === "auto" ? undefined : headerMode === "on";

  // HCM objects load through HCM Data Loader, not FBDI: pipe-delimited .dat files
  // inside per-object zips, and there is no Oracle .xlsm template for them at all.
  // Labelling those two buttons "CSV" and "FBDI Excel" on an Employee conversion
  // describes a format the tool does not produce for it — which is how a download
  // that routed somewhere else entirely read as a generation failure.
  const isHdl = (c: any) =>
    /(employee|worker|hcm|hdl|position|job|location)/i.test(
      String(c?.target_object || c?.template_name || c?.name || ""));

  // Whole-project verdict, for the BULK controls. Only when EVERY conversion is an
  // HCM object — a mixed project keeps the neutral FBDI wording rather than
  // mislabelling the other half.
  const allHdl = (conversions?.length ?? 0) > 0 && (conversions ?? []).every(isHdl);

  const downloadAllFbdi = async (fmt: "csv" | "template" = "csv") => {
    setDlAll(true);
    const objName = (c: Conversion) =>
      (c as any).target_object || (c as any).template_name || c.name;

    const setStage = (key: string, stage: GenStage) =>
      setGenProg((prev) => prev.map((p) => (p.key === key ? { ...p, stage } : p)));
    try {
      const convs = [...conversions].sort(
        (a, b) => ((a as any).planned_load_order ?? 99) - ((b as any).planned_load_order ?? 99),
      );
      const n = convs.length;
      if (!n) { flash("This engagement has no conversions yet."); return; }

      // Seed the progress panel (one row per object, in load order).
      setGenProg(convs.map((c, i) => ({ key: String(c.id), name: objName(c), order: i + 1, stage: "queued" as GenStage })));

      // Process objects with a small concurrency pool (they're independent), so
      // the 6-object supplier set runs in parallel instead of one-by-one. Each
      // worker maps (only if unmapped) then generates that object's FBDI output;
      // generate force-applies the stored gold standard so gold always wins.
      // Phase 1: ensure every source conversion is mapped (each has its OWN
      // mapping — heterogeneous sources map correctly). Generation is merged
      // per-interface, so we only MAP here, then merge+generate per object.
      const runOne = async (c: Conversion) => {
        const key = String(c.id);
        try {
          setStage(key, "mapping");
          let ms: any[] = [];
          try { ms = await MappingApi.list(key); } catch { ms = []; }
          if (!ms.some((m) => m.source_column)) {
            try { await MappingApi.suggest(key); } catch { /* keep going */ }
          }
          setStage(key, "done");
        } catch {
          setStage(key, "error");
        }
      };
      const POOL = 3;
      const queue = [...convs];
      const workers = Array.from({ length: Math.min(POOL, queue.length) }, async () => {
        while (queue.length) {
          const c = queue.shift();
          if (c) await runOne(c);
        }
      });
      await Promise.all(workers);

      // Phase 2: merge every interface's sources into ONE file (de-duplicated,
      // cleansed, validated), generated in the background so wide multi-source
      // objects can't hit the gateway timeout, then download the fast reuse-zip.
      const nObjs = new Set(convs.map((c) => objName(c))).size;
      // A project of HCM objects produces HDL .dat files, not FBDI anything. Saying
      // "Merging & generating FBDI Excel template files" over an Employee load names
      // the wrong loader while it runs, and then names the download the same way, so
      // the file on disk carries the wrong word too.
      const _allHdl = convs.length > 0 && convs.every((c) => isHdl(c));
      const _kind = _allHdl
        ? (fmt === "template" ? "HDL template" : "HDL .dat")
        : (fmt === "template" ? "FBDI Excel template" : "FBDI CSV");
      // CSV bundle is named _CSV; the filled Oracle workbooks keep _FBDI_templates.
      const _suffix = _allHdl
        ? (fmt === "template" ? "HDL_templates" : "DAT")
        : (fmt === "template" ? "FBDI_templates" : "CSV");
      setDlStatus(`Merging & generating ${nObjs} ${_kind} file${nObjs === 1 ? "" : "s"}…`);
      const results = await OutputApi.downloadAll(
        pid,
        `${(project?.name ?? "engagement").replace(/[^\w.-]+/g, "_")}_${_suffix}.zip`,
        fmt,
        (sec, done, total) => setDlStatus(`Merging & generating ${_kind} files… ${done}/${total} (${sec}s)`),
        headerFlag,
      );
      const failed = (results || []).filter((r) => !r.ready);
      flash(failed.length
        ? `Bundle downloaded. ${failed.length} interface(s) had issues: ${failed.map((f) => f.object).join(", ")}.`
        : `${_allHdl
              ? (fmt === "template" ? "HDL templates" : "HDL .dat bundle")
              : (fmt === "template" ? "FBDI Excel templates" : "FBDI CSV bundle")} generated and downloaded (${nObjs} merged interface file${nObjs === 1 ? "" : "s"}).`);
      refresh();
      loadRefStandards();
    } catch (e: any) {
      flash(
        e?.response?.status === 400
          ? "No conversions are ready yet — each needs a source file and a bound FBDI template."
          : "Couldn't build the FBDI bundle. Please try again.",
      );
    } finally { setDlAll(false); setDlStatus(null); setTimeout(() => setGenProg([]), 1500); }
  };

  // Bulk gold upload — each file is matched to its object (by filename) and
  // applied via learn-from-example to that conversion, so the tool learns
  // exactly what to populate / leave blank per object.
  const goldInputRef = React.useRef<HTMLInputElement>(null);
  const [goldBusy, setGoldBusy] = useState(false);
  const [goldStatus, setGoldStatus] = useState<string | null>(null);
  const [goldResults, setGoldResults] = useState<{ name: string; object: string | null; ok: boolean; msg: string }[]>([]);
  const _gn = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const matchGoldObject = (filename: string): string | null => {
    const n = filename.toLowerCase();
    if (/assignment/.test(n)) return "Supplier Site Assignment";
    if (/address/.test(n)) return "Supplier Address";
    if (/contact/.test(n)) return "Supplier Contacts";
    if (/bank/.test(n)) return "Supplier Banks";
    if (/site/.test(n)) return "Supplier Site";
    if (/supplier|vendor|import/.test(n)) return "Supplier Import";
    return null;
  };
  const uploadGoldAll = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setGoldBusy(true); setGoldResults([]);
    const results: { name: string; object: string | null; ok: boolean; msg: string }[] = [];
    try {
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const obj = matchGoldObject(f.name);
        const conv = obj ? conversions.find(
          (c) => _gn((c as any).target_object || (c as any).template_name || c.name) === _gn(obj)
        ) : null;
        if (!obj || !conv) {
          results.push({ name: f.name, object: obj, ok: false, msg: "no matching object" });
          setGoldResults([...results]); continue;
        }
        setGoldStatus(`Applying gold → ${obj} (${i + 1}/${files.length})`);
        try {
          const r = await ConversionsApi.learnFromExample(String(conv.id), { file: f });
          const l = r.learned;
          const msg = l
            ? `${l.mapped_count} mapped · ${l.default_count} defaults${l.suppressed_count ? ` · ${l.suppressed_count} blanked` : ""}`
            : "applied";
          results.push({ name: f.name, object: obj, ok: true, msg });
        } catch (e: any) {
          results.push({ name: f.name, object: obj, ok: false, msg: e?.response?.data?.detail || "failed" });
        }
        setGoldResults([...results]);
      }
      const okc = results.filter((r) => r.ok).length;
      flash(`Applied gold to ${okc}/${files.length} object${files.length === 1 ? "" : "s"} — regenerate outputs to apply.`);
      refresh();
      loadRefStandards();
    } finally {
      setGoldBusy(false); setGoldStatus(null);
      if (goldInputRef.current) goldInputRef.current.value = "";
    }
  };

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
    loadRefStandards();
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

  // A file-based / FBDI-download engagement doesn't use a live EBS source
  // connection, so we hide the EBS/Discovery controls for it.
  const fileBased =
    /fbdi/i.test((project as any)?.target_environment || "") ||
    (conversions.length > 0 && conversions.every((c) => (c as any).source_type === "dataset"));

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
              loading={busy === "chain"}
              onClick={async () => {
                setBusy("chain");
                try {
                  const r = await ProjectsApi.chainLoadOrder(pid);
                  flash(
                    r.created.length
                      ? `Mapped ${r.created.length} dependency link(s) across the load sequence`
                      : "Load sequence already mapped",
                  );
                  refresh();
                } catch {
                  flash("Couldn't map the load sequence");
                } finally { setBusy(null); }
              }}
            >
              <Network className="h-4 w-4" /> Map Load Sequence
            </Button>
            <Button
              variant="secondary"
              onClick={() => setShowModuleModal(true)}
            >
              <Wand2 className="h-4 w-4" /> Auto-populate
            </Button>
            <Button variant="secondary" disabled={dlAll} onClick={() => downloadAllFbdi("csv")}>
              <FolderDown className={cn("h-4 w-4", dlAll && "animate-pulse")} />
              {dlAll ? (dlStatus ?? "Working…") : allHdl ? "Download all DAT" : "Download all CSV"}
            </Button>
            <Button variant="secondary" disabled={dlAll} onClick={() => downloadAllFbdi("template")}
              title="Merge each interface's sources and download the filled-in Oracle FBDI Excel templates (.xlsm)">
              <FolderDown className={cn("h-4 w-4", dlAll && "animate-pulse")} />
              {dlAll ? (dlStatus ?? "Working…") : allHdl ? "Download all (HDL templates)" : "Download all (FBDI Excel)"}
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

      {/* Agentic plan (checkpoint preview) */}
      <Card className="mt-4">
        <CardHeader
          title={<><Wand2 className="mr-2 inline h-4 w-4 text-brand" />Agentic conversion plan (preview)</>}
          subtitle="Draft the map → generate → validate plan for every interface — review before anything runs"
          actions={
            <Button variant="secondary" disabled={planLoading} onClick={loadPlan}>
              <Wand2 className={cn("h-4 w-4", planLoading && "animate-pulse")} />
              {planLoading ? "Drafting…" : plan ? "Re-draft plan" : "Draft plan"}
            </Button>
          }
        />
        {plan && (
          <CardBody>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
              <Pill tone="neutral">{plan.object_count} objects</Pill>
              <Pill tone="neutral">{plan.total_steps} planned steps</Pill>
              <Pill tone="success">{plan.ready_count} ready</Pill>
              {plan.blocked_objects?.length > 0 && <Pill tone="danger">{plan.blocked_objects.length} blocked</Pill>}
              <span className="text-ink-muted">{plan.note}</span>
            </div>
            <div className="space-y-3">
              {(plan.objects || []).map((o: any) => (
                <div key={o.conversion_id} className="rounded-lg border border-line bg-white">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-ink">{o.target_object || o.name}</span>
                      <Pill tone={o.status === "Ready" ? "success" : o.status.startsWith("Blocked") ? "danger" : "warning"}>{o.status}</Pill>
                      {o.readiness && <span className="text-[11px] text-ink-muted">{o.readiness.score}/100</span>}
                    </div>
                    <span className="text-[11px] text-ink-subtle">{o.required_covered}/{o.required_total} required · {o.steps.length} step(s)</span>
                  </div>
                  <ol className="space-y-1 px-3 py-2">
                    {o.steps.map((s: any, i: number) => (
                      <li key={i} className="flex items-start gap-2 text-xs">
                        <span className={cn("mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px] font-bold",
                          s.blocker ? "bg-danger-subtle text-danger" : "bg-brand-subtle text-brand-dark")}>{s.blocker ? "!" : i + 1}</span>
                        <span>
                          <span className="font-medium text-ink">{s.action}</span>
                          <span className="text-ink-muted"> — {s.detail}</span>
                          <span className="ml-1 rounded bg-canvas px-1 py-0.5 text-[10px] text-ink-subtle">{s.layer}</span>
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <Button variant="primary" disabled title="Plan execution with per-object approval is the next slice">
                Approve &amp; run (coming soon)
              </Button>
              <span className="text-[11px] text-ink-muted">This is a checkpoint — nothing is mapped, generated or loaded until you approve.</span>
            </div>
          </CardBody>
        )}
      </Card>

      {/* Conversion Objects + Source Connection + Load Order */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Conversion Objects"
            subtitle={`${totals.total} object${totals.total === 1 ? "" : "s"} ordered by planned load sequence`}
            actions={conversions.length > 0 && (
              <div className="flex items-center gap-2">
                {/* Upload gold-standard outputs — each file is matched to its
                    object and applied (learn-from-example) to that conversion. */}
                <input
                  ref={goldInputRef}
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  multiple
                  className="hidden"
                  onChange={(e) => uploadGoldAll(e.target.files)}
                />
                <button
                  onClick={() => goldInputRef.current?.click()}
                  disabled={goldBusy || dlAll}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white px-2.5 py-1 text-[11px] font-semibold text-ink hover:bg-canvas disabled:opacity-50"
                  title="Upload one or more gold-standard output files (.xlsx). Each is matched to its object by filename and applied to that conversion — the tool learns exactly what to map, default, and leave blank."
                >
                  <Upload className={cn("h-3 w-3", goldBusy && "animate-pulse")} />
                  {goldBusy ? (goldStatus ?? "Applying gold…") : "Upload gold outputs"}
                </button>
                {/* Header row control for both bundle downloads. Auto is the
                    Oracle-correct default: Excel templates keep their column
                    labels, CSV bundles are headerless because the FBDI loader
                    reads a header line as a data row and rejects that record. */}
                <div className="inline-flex items-center gap-1 rounded-md border border-line bg-white p-0.5"
                     title="Header row in the generated files. Auto = Excel templates with headers, FBDI CSVs without (what Oracle expects). Override if you need them either way.">
                  <span className="px-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle">Header</span>
                  {([["auto", "Auto"], ["on", "On"], ["off", "Off"]] as const).map(([v, label]) => (
                    <button
                      key={v}
                      onClick={() => setHeaderMode(v)}
                      disabled={dlAll}
                      className={cn(
                        "rounded px-1.5 py-0.5 text-[11px] font-medium transition disabled:opacity-50",
                        headerMode === v ? "bg-brand text-white" : "text-ink-muted hover:text-ink",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {/* Generate every object's FBDI output and download them together
                    as one zip (ordered by the supplier load sequence). */}
                <button
                  onClick={() => downloadAllFbdi("csv")}
                  disabled={dlAll}
                  className="inline-flex items-center gap-1.5 rounded-md border border-brand/40 bg-brand-subtle px-2.5 py-1 text-[11px] font-semibold text-brand-dark hover:bg-brand-subtle/70 disabled:opacity-50"
                  title="For every object: apply your gold reference standard (if on file) + learnings + uploaded templates + deterministic Python (country/currency/UOM/flags) + rule-based matching, using AI only for the residual — then generate each FBDI output and download all as one .zip ordered by load sequence."
                >
                  <FolderDown className={cn("h-3 w-3", dlAll && "animate-pulse")} />
                  {dlAll ? (dlStatus ?? "Working…") : "Generate all & download (.zip)"}
                </button>
                <button
                  onClick={() => downloadAllFbdi("template")}
                  disabled={dlAll}
                  className="inline-flex items-center gap-1.5 rounded-md border border-brand/40 bg-white px-2.5 py-1 text-[11px] font-semibold text-brand-dark hover:bg-brand-subtle/40 disabled:opacity-50"
                  title="Same pipeline, but write each interface's merged data INTO the real Oracle FBDI Excel template (POZ_SUPPLIERS_INT, EGP_STRUCTURES_INTERFACE, …) and download the filled templates as one .zip."
                >
                  <FolderDown className={cn("h-3 w-3", dlAll && "animate-pulse")} />
                  {dlAll ? (dlStatus ?? "Working…") : allHdl ? "HDL templates (.zip)" : "FBDI Excel templates (.zip)"}
                </button>
                {!fileBased && (
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
                )}
              </div>
            )}
          />
          {genProg.length > 0 && (() => {
            const done = genProg.filter((p) => p.stage === "done").length;
            const failed = genProg.filter((p) => p.stage === "error").length;
            const pctDone = Math.round(((done + failed) / genProg.length) * 100);
            const stageTone: Record<GenStage, string> = {
              queued: "text-ink-subtle", mapping: "text-brand-dark",
              generating: "text-brand-dark", done: "text-emerald-600", error: "text-danger",
            };
            return (
              <div className="border-b border-line bg-brand-subtle/10 px-4 py-3 text-[11px]">
                <div className="mb-2 flex items-center justify-between">
                  <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                    <RefreshCw className="h-3.5 w-3.5 animate-spin text-brand" />
                    Generating {allHdl ? "HDL bundle" : "FBDI bundle"} — {done}/{genProg.length} done{failed ? ` · ${failed} failed` : ""}
                  </span>
                  <span className="font-mono tabular-nums text-ink-muted">{dlStatus ?? `${pctDone}%`}</span>
                </div>
                <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-line">
                  <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${pctDone}%` }} />
                </div>
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {genProg.map((p) => (
                    <div key={p.key} className="flex items-center gap-2 rounded border border-line bg-white px-2 py-1">
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand/10 font-mono text-[9px] text-brand-dark">{p.order}</span>
                      <span className="min-w-0 flex-1 truncate text-ink">{p.name}</span>
                      {p.stage === "done" ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                        : p.stage === "error" ? <AlertCircle className="h-3.5 w-3.5 shrink-0 text-danger" />
                        : (p.stage === "mapping" || p.stage === "generating") ? <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" />
                        : <Clock className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />}
                      <span className={cn("shrink-0 text-[10px]", stageTone[p.stage])}>{GEN_STAGE_LABEL[p.stage]}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
          {refStandards.length > 0 && (
            <div className="border-b border-line bg-brand-subtle/15 px-4 py-2 text-[11px]">
              <div className="mb-1 flex items-center gap-1.5 font-semibold text-brand-dark">
                <Wand2 className="h-3 w-3" />
                Reference standards on file — applied automatically on Generate &amp; download (no re-upload, no extra step)
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-ink-muted">
                {refStandards.map((r) => (
                  <span key={r.business_object} className="inline-flex items-center gap-1">
                    <span className="font-medium text-ink">{r.business_object}</span>
                    <span className="font-mono text-[10px]">
                      {r.column_mappings}m · {r.defaults}d · {r.suppressions}s
                    </span>
                  </span>
                ))}
              </div>
              <div className="mt-0.5 text-[10px] text-ink-subtle">
                m = mapped columns · d = constant defaults · s = suppressed (kept blank). Uploading gold again overrides the stored standard for that object.
              </div>
            </div>
          )}
          {goldResults.length > 0 && (
            <div className="border-b border-line bg-canvas px-4 py-2 text-[11px]">
              <div className="mb-1 font-semibold text-ink">Gold applied — regenerate to see updated output:</div>
              <div className="flex flex-col gap-0.5">
                {goldResults.map((r, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className={r.ok ? "text-emerald-600" : "text-danger"}>{r.ok ? "✓" : "✕"}</span>
                    <span className="truncate font-mono text-ink-muted" style={{ maxWidth: "16rem" }}>{r.name}</span>
                    <ArrowRight className="h-3 w-3 shrink-0 text-ink-subtle" />
                    <span className="font-medium text-ink">{r.object || "unmatched"}</span>
                    <span className="text-ink-subtle">· {r.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
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
                  <th>Output</th>
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
                      {(() => {
                        const rs = refStdFor(c.target_object);
                        return rs ? (
                          <div
                            className="mt-0.5 inline-flex items-center gap-1 rounded-full bg-brand-subtle/40 px-1.5 py-0.5 text-[9.5px] font-medium text-brand-dark"
                            title={`A gold reference standard is on file for ${rs.business_object} and is auto-applied to this conversion: ${rs.column_mappings} mapped columns, ${rs.defaults} constant defaults, ${rs.suppressions} suppressed. Uploading gold again overrides it.`}
                          >
                            <Wand2 className="h-2.5 w-2.5" />
                            gold on file · {rs.column_mappings}m {rs.defaults}d {rs.suppressions}s
                          </div>
                        ) : null;
                      })()}
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
                    <td>
                      <select
                        className="input !h-7 !w-auto !py-0 !text-[11px]"
                        value={(c as any).output_mode || "fusion_load"}
                        onChange={(e) => setOutputMode(c, e.target.value)}
                        title="How this conversion is delivered"
                      >
                        {/* Same value, honest label. An HCM object does not load
                            through FBDI at all — it goes to HCM Data Loader as
                            pipe-delimited .dat files — so calling the delivery mode
                            "FBDI download" on an Employee row describes the wrong
                            loader entirely. The stored value stays fbdi_download
                            because it means "file download, not a live Fusion load";
                            only what the analyst reads changes. */}
                        <option value="fbdi_download">
                          {isHdl(c) ? "HDL download" : "FBDI download"}
                        </option>
                        <option value="fusion_load">Load to Fusion</option>
                      </select>
                    </td>
                    <td className="text-right">
                      <div className="inline-flex items-center gap-1">
                        {((c as any).output_mode === "fbdi_download") && (
                          <>
                            <button
                              onClick={() => downloadFbdi(c)}
                              disabled={dl === String(c.id)}
                              title={isHdl(c)
                                ? "Generate & download the HDL .dat files (one zip per object, in load order)"
                                : "Generate & download the FBDI CSV bundle"}
                              className="btn-ghost h-7 px-2 text-xs disabled:opacity-50"
                            >
                              {/* "CSV" and "FBDI Excel", not "FBDI" and "Excel".
                                  Both buttons produce FBDI — one the headerless CSV
                                  bundle Oracle actually loads, the other the filled
                                  .xlsm template. Labelling only the first "FBDI"
                                  implied the second was something else. */}
                              <Download className="h-3 w-3" /> {isHdl(c) ? "DAT files" : "CSV"}
                            </button>
                            <button
                              onClick={() => downloadTemplate(c)}
                              disabled={dlT === String(c.id)}
                              title={isHdl(c)
                                ? "Generate & download the filled-in HDL template workbook"
                                : "Generate & download the filled-in Oracle FBDI Excel template"}
                              className="btn-ghost h-7 px-2 text-xs disabled:opacity-50"
                            >
                              <Download className="h-3 w-3" />{" "}
                              {dlT === String(c.id)
                                ? "…" : isHdl(c) ? "HDL Template" : "FBDI Excel"}
                            </button>
                          </>
                        )}
                        <Link to={`/conversions/${c.id}`} className="btn-ghost h-7 px-2 text-xs">
                          Open <ArrowRight className="h-3 w-3" />
                        </Link>
                        <button
                          onClick={() => deleteConversion({ id: String(c.id), name: c.name })}
                          title="Delete conversion"
                          className="rounded p-1 text-ink-subtle hover:bg-danger-subtle hover:text-danger"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {/* Right sidebar: Source Connection + Load Order */}
        <div className="flex flex-col gap-4">
          {!fileBased && (
            <SourceConnectionCard
              projectId={pid}
              projectSourceSystem={(project as any).source_system}
            />
          )}
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

      {/* Discovery — only relevant for live-source (EBS) engagements */}
      {!fileBased && (
        <div className="mt-4">
          <DiscoveryPanel projectId={pid} hasConnection={hasConnection} />
        </div>
      )}

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
