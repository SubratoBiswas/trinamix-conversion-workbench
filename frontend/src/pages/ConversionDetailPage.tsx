import React, { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, Database, FileSpreadsheet, Sparkles, ShieldCheck,
  ListChecks, Play, Download, FileOutput, ArrowRight, Workflow as WfIcon,
  Eye, Cloud, GitBranch, CheckCircle2, Clock, XCircle, Loader2, Zap, Table2,
  Upload, Wand2,
} from "lucide-react";
import {
  ConversionsApi, CutoverApi, DatasetsApi, FbdiApi, LearningApi, LoadApi, MappingApi,
  OutputApi, ProjectsApi, QualityApi,
} from "@/api";
import type { ReferenceStandard } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { COAEngine } from "@/components/coa/COAEngine";
import { PromoteToEnvironmentModal } from "@/components/cutover/PromoteToEnvironmentModal";
import { cn, formatDate, statusTone } from "@/lib/utils";
import type {
  Conversion, ConvertedOutput, Dataset, Environment, EnvironmentRun,
  FBDITemplate, LoadRun, MappingSuggestion, Project, ValidationIssue,
} from "@/types";

// Match any conversion whose target object signals a Chart-of-Accounts /
// GL Coding Combinations conversion. Case-insensitive substring match
// so synonyms like "Coding Combinations" / "GL Account" / "COA" trigger
// the specialised multi-segment composer.
const _COA_TARGET_HINTS = [
  "chart of accounts", "coa", "gl account", "coding combination",
  "general ledger account", "natural account",
];
function isCOAConversion(targetObject?: string | null): boolean {
  if (!targetObject) return false;
  const t = targetObject.toLowerCase();
  return _COA_TARGET_HINTS.some((h) => t.includes(h));
}

// Map a conversion's target object to its fan-out catalog key, so the detail
// screen can show the full FBDI load-sequence (e.g. supplier → 7 files).
function seqKeyForTarget(target?: string | null, bo?: string | null): string | null {
  const s = `${target || ""} ${bo || ""}`.toLowerCase();
  if (/supplier|vendor/.test(s)) return "supplier";
  if (/customer|client/.test(s)) return "customer";
  if (/\bitem\b|product|material/.test(s)) return "item";
  if (/journal|\bgl\b/.test(s)) return "gl_journal";
  if (/autoinvoice|receivable/.test(s)) return "ar_invoice";
  if (/payable|\bap\b/.test(s)) return "ap_invoice";
  return null;
}

/**
 * Operations page for a single Conversion object. The user runs AI mapping,
 * cleansing, validation, output generation, and load simulation from here.
 */
export const ConversionDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const cid = id;
  const nav = useNavigate();

  const [conv, setConv] = useState<Conversion | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [template, setTemplate] = useState<FBDITemplate | null>(null);

  const [targetFields, setTargetFields] = useState<import("@/types").FBDIField[]>([]);
  const [mappings, setMappings] = useState<MappingSuggestion[]>([]);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [outputs, setOutputs] = useState<ConvertedOutput[]>([]);
  const [loadRuns, setLoadRuns] = useState<LoadRun[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [envRuns, setEnvRuns] = useState<EnvironmentRun[]>([]);

  const [fbdiTemplates, setFbdiTemplates] = useState<FBDITemplate[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  // Gold reference standards on file (per object), loaded from the DB. Lets this
  // page show whether a standard already exists for this conversion's object
  // (uploaded here or on the project screen) — it's auto-applied at generate.
  const [refStandards, setRefStandards] = useState<ReferenceStandard[]>([]);
  const goldInputRef = React.useRef<HTMLInputElement>(null);
  // Load-sequence map: the full FBDI file set for this object (Req 2).
  const [seqSteps, setSeqSteps] = useState<{ label: string; load_order: number }[]>([]);
  const [siblings, setSiblings] = useState<Conversion[]>([]);

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };

  // Fetch the object's FBDI load sequence + the engagement's conversions so we
  // can render the connected file-set map (supplier → 7 files, etc.).
  useEffect(() => {
    if (!conv) { setSeqSteps([]); setSiblings([]); return; }
    const key = seqKeyForTarget(conv.target_object, template?.business_object);
    if (!key) { setSeqSteps([]); setSiblings([]); return; }
    let alive = true;
    Promise.all([
      ConversionsApi.objectTypes().catch(() => [] as any[]),
      // Resolve step→conversion within THIS engagement only, so the map and
      // Prev/Next never jump the user into a different project.
      ConversionsApi.list({ project_id: String(conv.project_id) }).catch(() => [] as Conversion[]),
    ]).then(([types, sibs]) => {
      if (!alive) return;
      setSeqSteps(((types as any[]).find((t) => t.key === key)?.steps) || []);
      setSiblings((sibs as Conversion[]) || []);
    });
    return () => { alive = false; };
  }, [conv?.id, template?.id]);

  // Upload a CSV/Excel file, create a dataset, and link it to this conversion
  const handleDatasetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy("upload_dataset");
    try {
      const newDataset = await DatasetsApi.upload(file);
      await ConversionsApi.update(cid, { dataset_id: newDataset.id });
      flash("Dataset uploaded and linked");
      loadAll();
    } catch (err: any) {
      flash(`Upload failed: ${err?.response?.data?.detail || err?.message}`);
    } finally { setBusy(null); }
  };

  // Link an existing FBDI template to this conversion
  const handleTemplateChange = async (templateId: string) => {
    if (!templateId) return;
    setBusy("link_template");
    try {
      const tpl = fbdiTemplates.find((t) => t.id === templateId);
      await ConversionsApi.update(cid, {
        template_id: templateId,
        target_object: tpl?.business_object || tpl?.name,
      });
      // Learn the choice: for a file-based conversion, remember this file's
      // column signature → chosen FBDI template so future similar files
      // auto-recommend it (the tool learns from the user's override).
      if (conv?.dataset_id) {
        DatasetsApi.classifyLearn(conv.dataset_id, {
          template_id: templateId,
          target_object: tpl?.business_object || tpl?.name,
        }).catch(() => {});
      }
      flash("Target FBDI updated — the tool will remember this for similar files");
      loadAll();
    } catch (err: any) {
      flash(`Failed: ${err?.response?.data?.detail || err?.message}`);
    } finally { setBusy(null); }
  };

  const loadAll = async () => {
    const c = await ConversionsApi.get(cid);
    setConv(c);
    LearningApi.referenceStandards().then(setRefStandards).catch(() => setRefStandards([]));
    if (c.project_id) {
      ProjectsApi.get(c.project_id).then(setProject);
      CutoverApi.environments(c.project_id).then(setEnvironments).catch(() => setEnvironments([]));
    }
    if (c.dataset_id) DatasetsApi.get(c.dataset_id).then((d) => setDataset(d));
    else setDataset(null);
    if (c.template_id) FbdiApi.get(c.template_id).then((t) => setTemplate(t));
    FbdiApi.list().then(setFbdiTemplates).catch(() => {});
    CutoverApi.runsForConversion(cid).then(setEnvRuns).catch(() => setEnvRuns([]));
    if (c.template_id) {
      FbdiApi.fields(c.template_id).then(setTargetFields).catch(() => setTargetFields([]));
      MappingApi.list(cid).then(setMappings).catch(() => setMappings([]));
      QualityApi.cleansing(cid).then((cl) =>
        QualityApi.validation(cid).then((vl) => setIssues([...cl, ...vl]))
      ).catch(() => {});
      LoadApi.runs(cid).then(setLoadRuns).catch(() => setLoadRuns([]));
    }
  };
  useEffect(() => { loadAll(); }, [cid]);

  if (!conv) return <PageLoader />;

  const runOp = async (op: string, fn: () => Promise<any>, successMsg: string) => {
    setBusy(op);
    try {
      await fn();
      flash(successMsg);
      loadAll();
    } catch (e: any) {
      flash(`Error: ${e?.response?.data?.detail || e?.message || "operation failed"}`);
    } finally { setBusy(null); }
  };

  // Upload a gold-standard output for THIS object. It's learned + stored as a
  // reusable reference standard (same store used by the project screen) and
  // force-applied at generate time. Overrides any previously stored standard.
  const uploadGold = async (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    setBusy("gold");
    try {
      const r = await ConversionsApi.learnFromExample(cid, { file: f });
      const l = r.learned;
      flash(l
        ? `Gold applied — ${l.mapped_count} mapped, ${l.default_count} defaults, ${l.suppressed_count ?? 0} suppressed. Regenerate to refresh the output.`
        : "Gold applied.");
      loadAll();
    } catch (e: any) {
      flash(`Gold upload failed: ${e?.response?.data?.detail || e?.message}`);
    } finally {
      setBusy(null);
      if (goldInputRef.current) goldInputRef.current.value = "";
    }
  };

  // A reference standard is "on file" for this object if one is stored under the
  // conversion's target object or the template's business object (e.g. "Supplier").
  const _rn = (s?: string | null) => (s || "").trim().toLowerCase();
  const refStd = refStandards.find(
    (r) => _rn(r.business_object) === _rn(conv.target_object)
      || _rn(r.business_object) === _rn(template?.business_object),
  );

  return (
    <>
      <PageTitle
        title={conv.name}
        subtitle={
          <span className="flex items-center gap-2 text-[12.5px]">
            {project && (
              <>
                <Link to={`/projects/${project.id}`} className="text-brand-dark hover:underline">
                  {project.name}
                </Link>
                <ArrowRight className="h-3 w-3 text-ink-subtle" />
              </>
            )}
            <span>{conv.target_object || "—"}</span>
            <Pill tone={statusTone(conv.status)}>{conv.status.replace("_", " ")}</Pill>
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Link to="/conversions" className="btn-ghost">
              <ArrowLeft className="h-4 w-4" /> All conversions
            </Link>
            {project && (
              <Button onClick={() => setPromoteOpen(true)}>
                <ArrowRight className="h-4 w-4" /> Promote to environment
              </Button>
            )}
          </div>
        }
      />

      {/* Environment progression strip — shows DEV → QA → UAT → PROD status */}
      <EnvironmentStrip
        conversion={conv}
        environments={environments}
        runs={envRuns}
        onPromote={() => setPromoteOpen(true)}
      />

      {/* Bindings strip — shows source + target + lets user fix gaps */}
      <Card className="mb-4">
        <CardBody>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {!conv.dataset_id ? (
              /* ── EBS live source card ── */
              <div className="flex items-center gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-100 text-emerald-600">
                  <Zap className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-emerald-700">Oracle EBS Live Source</div>
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[9.5px] font-semibold text-emerald-700">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      LIVE
                    </span>
                  </div>
                  {conv.ebs_table_hint ? (
                    <div className="flex items-center gap-1.5">
                      <Table2 className="h-3 w-3 text-emerald-600 shrink-0" />
                      <span className="font-mono text-[11px] font-semibold text-emerald-800">{conv.ebs_table_hint}</span>
                      <span className="text-[10.5px] text-emerald-600">· streamed at runtime</span>
                    </div>
                  ) : (
                    <span className="text-[11px] text-emerald-700">Connected — table resolved at runtime</span>
                  )}
                </div>
              </div>
            ) : (
              /* ── Uploaded dataset card ── */
              <div className="flex items-center gap-3 rounded-md border border-line bg-canvas px-3 py-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-50 text-emerald-600">
                  <Database className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Source dataset</div>
                  {dataset ? (
                    <div className="flex items-center gap-2">
                      <Link to={`/datasets/${dataset.id}/prepare`} className="truncate text-sm font-semibold text-ink hover:text-brand-dark">
                        {dataset.name}
                        <span className="ml-1.5 font-mono text-[10.5px] text-ink-muted">
                          {dataset.row_count.toLocaleString()} × {dataset.column_count}
                        </span>
                      </Link>
                      <label className="shrink-0 cursor-pointer rounded px-1.5 py-0.5 text-[11px] font-medium text-ink-muted hover:bg-canvas hover:text-ink" title="Replace dataset">
                        <input type="file" className="hidden" accept=".csv,.xlsx,.xls" onChange={handleDatasetUpload} />
                        ↩ Replace
                      </label>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-sm italic text-ink-subtle">Awaiting source file</span>
                      <label className={cn(
                        "shrink-0 cursor-pointer rounded px-2 py-0.5 text-[11px] font-medium",
                        busy === "upload_dataset"
                          ? "text-ink-subtle"
                          : "bg-brand text-white hover:bg-brand-dark"
                      )}>
                        <input type="file" className="hidden" accept=".csv,.xlsx,.xls" onChange={handleDatasetUpload} disabled={busy === "upload_dataset"} />
                        {busy === "upload_dataset" ? "Uploading…" : "↑ Upload"}
                      </label>
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className="flex items-center gap-3 rounded-md border border-line bg-canvas px-3 py-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
                <FileSpreadsheet className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Target FBDI</div>
                {template ? (
                  <div className="flex items-center gap-2">
                    <Link to={`/fbdi/${template.id}`} className="truncate text-sm font-semibold text-ink hover:text-brand-dark">
                      {template.name}
                      {template.business_object && (
                        <span className="ml-1.5 font-mono text-[10.5px] text-ink-muted">{template.business_object}</span>
                      )}
                    </Link>
                    {fbdiTemplates.length > 0 && (
                      <select
                        className="shrink-0 cursor-pointer rounded border border-line bg-canvas px-1.5 py-0.5 text-[11px] text-ink-muted hover:border-brand"
                        defaultValue=""
                        onChange={(e) => handleTemplateChange(e.target.value)}
                        disabled={busy === "link_template"}
                      >
                        <option value="" disabled>Change</option>
                        {fbdiTemplates.map(t => (
                          <option key={t.id} value={t.id}>{t.name}</option>
                        ))}
                      </select>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="text-sm italic text-ink-subtle">No FBDI selected</span>
                    {fbdiTemplates.length > 0 && (
                      <select
                        className="shrink-0 cursor-pointer rounded border border-brand bg-brand px-2 py-0.5 text-[11px] font-medium text-white hover:bg-brand-dark"
                        defaultValue=""
                        onChange={(e) => handleTemplateChange(e.target.value)}
                        disabled={busy === "link_template"}
                      >
                        <option value="" disabled>Select template</option>
                        {fbdiTemplates.map(t => (
                          <option key={t.id} value={t.id}>{t.name}</option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* Load-sequence map — all FBDI files for this object, connected (Req 2) */}
      {seqSteps.length > 1 && (
        <Card className="mb-4">
          <CardHeader
            title="Load Sequence"
            subtitle={`All ${seqSteps.length} FBDI files for this object, connected in Fusion load order`}
          />
          <CardBody>
            <div className="flex flex-wrap items-center gap-1.5">
              {seqSteps.map((s, i) => {
                const sc = siblings.find(
                  (x) =>
                    (x.target_object || "").toLowerCase() === s.label.toLowerCase() ||
                    (x.template_name || "").toLowerCase() === s.label.toLowerCase() ||
                    (x.planned_load_order === s.load_order &&
                      seqKeyForTarget(x.target_object, x.template_name) ===
                        seqKeyForTarget(conv?.target_object, template?.business_object))
                );
                const isCurrent = !!sc && String(sc.id) === String(conv?.id);
                const node = (
                  <div
                    className={cn(
                      "flex min-w-[128px] flex-col rounded-md border px-3 py-2 transition",
                      isCurrent
                        ? "border-brand bg-brand-subtle"
                        : sc
                        ? "border-line bg-white hover:border-brand"
                        : "border-dashed border-line bg-canvas"
                    )}
                  >
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-subtle">Step {s.load_order}</span>
                    <span className={cn("truncate text-[12.5px] font-medium", sc ? "text-ink" : "text-ink-subtle")}>{s.label}</span>
                    <span className="mt-0.5 text-[10px]">
                      {isCurrent ? (
                        <span className="font-semibold text-brand-dark">● current</span>
                      ) : sc ? (
                        <span className="text-success">✓ generated</span>
                      ) : (
                        <span className="text-ink-subtle">not generated</span>
                      )}
                    </span>
                  </div>
                );
                return (
                  <React.Fragment key={s.label}>
                    {sc && !isCurrent ? <Link to={`/conversions/${sc.id}`}>{node}</Link> : node}
                    {i < seqSteps.length - 1 && <ArrowRight className="h-4 w-4 shrink-0 text-ink-subtle" />}
                  </React.Fragment>
                );
              })}
            </div>
            {(() => {
              // Step through THIS engagement's files for the same object, in
              // Fusion load order — robust regardless of template naming, and
              // it never leaves the engagement.
              const curKey = seqKeyForTarget(conv?.target_object, template?.business_object);
              const set = siblings
                .filter((c) => seqKeyForTarget(c.target_object, c.template_name) === curKey)
                .sort((a, b) => (a.planned_load_order ?? 999) - (b.planned_load_order ?? 999));
              const idx = set.findIndex((c) => String(c.id) === String(conv?.id));
              const prevC = idx > 0 ? set[idx - 1] : undefined;
              const nextC = idx >= 0 && idx < set.length - 1 ? set[idx + 1] : undefined;
              return (
                <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
                  <Button variant="secondary" disabled={!prevC} onClick={() => prevC && nav(`/conversions/${prevC.id}`)}>
                    <ArrowLeft className="h-4 w-4" /> Previous file
                  </Button>
                  <span className="text-[11px] text-ink-muted">
                    {idx >= 0 ? `File ${idx + 1} of ${set.length} in this engagement` : `${set.length} file(s) in this engagement`}
                  </span>
                  <Button variant="primary" disabled={!nextC} onClick={() => nextC && nav(`/conversions/${nextC.id}`)}>
                    Next file <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              );
            })()}
          </CardBody>
        </Card>
      )}

      {/* Action toolbar */}
      <Card className="mb-4">
        <CardHeader title="Conversion Pipeline" subtitle="Run each stage in order, or jump to the dedicated workspace" />
        <CardBody>
          <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                loading={busy === "ai_map"}
                onClick={() => runOp("ai_map",
                  async () => { await MappingApi.suggest(cid); nav(`/mappings?conversion=${cid}`); },
                  "AI mapping run — opening Mapping Review"
                )}
              >
                <Sparkles className="h-4 w-4" /> AI Auto Map
              </Button>
              <Button
                variant="secondary"
                loading={busy === "cleansing"}
                onClick={() => runOp("cleansing",
                  () => QualityApi.runCleansing(cid),
                  "Cleansing analysis complete"
                )}
              >
                <ShieldCheck className="h-4 w-4" /> Run Cleansing
              </Button>
              <Button
                variant="secondary"
                loading={busy === "validate"}
                onClick={() => runOp("validate",
                  () => QualityApi.runValidation(cid),
                  "Validation complete"
                )}
              >
                <ListChecks className="h-4 w-4" /> Run Validation
              </Button>
              <Button
                variant="secondary"
                loading={busy === "output"}
                onClick={() => runOp("output",
                  () => OutputApi.generate(cid, "csv"),
                  "Output generated"
                )}
              >
                <FileOutput className="h-4 w-4" /> Generate Output
              </Button>
              <Button
                variant="secondary"
                loading={busy === "load"}
                onClick={() => runOp("load",
                  () => LoadApi.simulate(cid),
                  "Load simulated"
                )}
              >
                <Play className="h-4 w-4" /> Simulate Load
              </Button>
              <button
                className="btn-ghost"
                onClick={async () => {
                  try {
                    const outs = await OutputApi.list(cid);
                    if (!outs.length) { flash("No output yet — click Generate Output first"); return; }
                    await OutputApi.download(cid, outs[0].output_file_name);
                  } catch {
                    flash("Download failed — generate output first");
                  }
                }}
              >
                <Download className="h-4 w-4" /> Download Output
              </button>
              {/* Upload a gold-standard output for this object — learned, stored,
                  and force-applied at generate (same logic as the project screen). */}
              <input
                ref={goldInputRef}
                type="file"
                accept=".xlsx,.xls,.csv"
                className="hidden"
                onChange={(e) => uploadGold(e.target.files)}
              />
              <Button
                variant="secondary"
                loading={busy === "gold"}
                onClick={() => goldInputRef.current?.click()}
                title="Upload the gold-standard output for this object. The tool learns exactly what to map, default, and leave blank, stores it as a reusable reference standard, and applies it automatically at generate time. Overrides any previously stored standard for this object."
              >
                <Upload className="h-4 w-4" /> {refStd ? "Replace gold" : "Upload gold"}
              </Button>
            </div>
            {refStd && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-brand/25 bg-brand-subtle/15 px-3 py-2 text-[11.5px]">
                <Wand2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-dark" />
                <div>
                  <span className="font-semibold text-ink">
                    Gold reference standard on file for {refStd.business_object} — applied automatically at generate
                  </span>
                  <span className="ml-1 text-ink-muted">
                    ({refStd.column_mappings} mapped columns · {refStd.defaults} constant defaults · {refStd.suppressions} suppressed). No re-upload needed — uploading again overrides it.
                  </span>
                </div>
              </div>
            )}
        </CardBody>
      </Card>

      {/* COA Engine — specialised multi-segment composer, only when the
          conversion's target object signals it's a Chart-of-Accounts /
          GL Coding Combinations conversion. Embedded inline rather than
          spawning a separate route per the earlier UI guidance. */}
      {isCOAConversion(conv.target_object) && (
        <COAEngine
          conversionId={cid}
          dataset={dataset as any}
        />
      )}

      {/* Status grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Mapping summary */}
        <Card>
          <CardHeader
            title={<><Sparkles className="mr-2 inline h-4 w-4 text-brand" />Mappings</>}
            subtitle={targetFields.length > 0 ? `${targetFields.length} target field(s)` : `${mappings.length} suggestion(s)`}
            actions={
              <Link to={`/mappings?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Review <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            {(() => {
              const activeIds = new Set(targetFields.map(f => f.id));
              const scoped = targetFields.length > 0
                ? mappings.filter(m => activeIds.has(m.target_field_id))
                : mappings;
              return (
                <Stat
                  items={[
                    { label: "Auto-mapped",   value: scoped.filter(m => m.source_column).length, tone: "text-info" },
                    { label: "Approved",       value: scoped.filter(m => m.status === "approved").length, tone: "text-success" },
                    { label: "Required gaps",  value: scoped.filter(m => m.target_required && !m.source_column).length, tone: "text-danger" },
                  ]}
                />
              );
            })()}
          </CardBody>
        </Card>

        {/* Quality issues */}
        <Card>
          <CardHeader
            title={<><ShieldCheck className="mr-2 inline h-4 w-4 text-warning" />Quality Issues</>}
            subtitle={`${issues.length} total issue(s)`}
            actions={
              <Link to={`/validation?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Review <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            <Stat
              items={[
                { label: "Cleansing", value: issues.filter(i => i.category === "cleansing").length, tone: "text-info" },
                { label: "Validation", value: issues.filter(i => i.category === "validation").length, tone: "text-warning" },
                { label: "Critical",  value: issues.filter(i => i.severity === "critical" || i.severity === "error").length, tone: "text-danger" },
              ]}
            />
          </CardBody>
        </Card>

        {/* Output */}
        <Card>
          <CardHeader
            title={<><Eye className="mr-2 inline h-4 w-4 text-brand" />Output</>}
            subtitle={outputs.length === 0 ? "Not generated" : `${outputs.length} version(s)`}
            actions={
              <Link to={`/conversions/${cid}/output`} className="btn-ghost h-7 px-2 text-xs">
                Preview <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            {outputs.length === 0 ? (
              <div className="text-xs text-ink-muted">No output generated yet — run "Generate Output" above.</div>
            ) : (
              <div className="space-y-1">
                {outputs.slice(0, 3).map(o => (
                  <div key={o.id} className="flex items-center justify-between rounded-md bg-canvas px-2 py-1.5 text-xs">
                    <span className="truncate font-mono">{o.output_file_name}</span>
                    <span className="text-ink-muted">{o.row_count.toLocaleString()} rows</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Load runs */}
        <Card>
          <CardHeader
            title={<><Cloud className="mr-2 inline h-4 w-4 text-info" />Load Runs</>}
            subtitle={`${loadRuns.length} run(s)`}
            actions={
              <Link to={`/load?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Dashboard <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            {loadRuns.length === 0 ? (
              <div className="text-xs text-ink-muted">No load runs yet.</div>
            ) : (
              <div className="space-y-1">
                {loadRuns.slice(0, 3).map(r => (
                  <div key={r.id} className="flex items-center justify-between rounded-md bg-canvas px-2 py-1.5 text-xs">
                    <span className="font-mono">#{r.id} · {r.run_type}</span>
                    <span className="text-success">{r.passed_count} ✓</span>
                    <span className="text-danger">{r.failed_count} ✕</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}

      {/* Promote-to-environment modal */}
      {project && (
        <PromoteToEnvironmentModal
          open={promoteOpen}
          onClose={() => setPromoteOpen(false)}
          conversion={conv}
          project={project}
          onPromoted={(run) => {
            flash(`Promoted to ${run.environment_name}`);
            loadAll();
          }}
        />
      )}
    </>
  );
};

// ─────── Environment progression strip (DEV → QA → UAT → PROD) ───────

const DEFAULT_ENVS: Environment[] = [
  { id: -1, name: "DEV",  sort_order: 1, sox_controlled: 0 } as Environment,
  { id: -2, name: "QA",   sort_order: 2, sox_controlled: 0 } as Environment,
  { id: -3, name: "UAT",  sort_order: 3, sox_controlled: 0 } as Environment,
  { id: -4, name: "PROD", sort_order: 4, sox_controlled: 1 } as Environment,
];

const EnvironmentStrip: React.FC<{
  conversion: Conversion;
  environments: Environment[];
  runs: EnvironmentRun[];
  onPromote: () => void;
}> = ({ conversion, environments, runs, onPromote }) => {
  const sorted = environments.length > 0
    ? [...environments].sort((a, b) => a.sort_order - b.sort_order)
    : DEFAULT_ENVS;

  // Map env_id → most-recent run for display.
  const runByEnvId = new Map<number, EnvironmentRun>();
  for (const r of runs) {
    const existing = runByEnvId.get(r.environment_id);
    if (!existing || r.id > existing.id) runByEnvId.set(r.environment_id, r);
  }

  // For DEV, derive status from the conversion itself.
  const devStatus =
    conversion.status === "loaded" ? "complete" :
    conversion.status === "failed" ? "failed" :
    ["draft", "mapping_suggested", "awaiting_approval", "validated", "output_generated"]
      .includes(conversion.status) ? "running" :
    "pending";

  return (
    <Card className="mb-4">
      <CardBody className="!p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Environment progression
          </div>
          <button onClick={onPromote} className="text-[11px] font-medium text-brand-dark hover:underline">
            Promote →
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          {sorted.map((env, idx) => {
            const isDev = env.name === "DEV";
            const run = runByEnvId.get(env.id);
            const status = isDev ? devStatus : (run?.status ?? "pending");
            const tone = STATUS_INDICATOR[status] || STATUS_INDICATOR.pending;
            const Icon = tone.icon;

            return (
              <React.Fragment key={env.id}>
                <div
                  className={cn(
                    "flex flex-1 items-center gap-2 rounded-md border px-2.5 py-1.5",
                    tone.cardClass,
                  )}
                >
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", tone.iconClass, tone.spin && "animate-spin")} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[11.5px] font-bold tracking-wider text-ink">{env.name}</div>
                    <div className="text-[10px] text-ink-muted">
                      {isDev && run === undefined ? "build env" : status}
                      {run?.dataset_name && (
                        <span className="ml-1 truncate text-ink-subtle">· {run.dataset_name}</span>
                      )}
                    </div>
                  </div>
                  {env.sox_controlled === 1 && (
                    <ShieldCheck className="h-3 w-3 shrink-0 text-warning" />
                  )}
                </div>
                {idx < sorted.length - 1 && (
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
};

const STATUS_INDICATOR: Record<string, {
  cardClass: string; iconClass: string; icon: React.ElementType; spin?: boolean;
}> = {
  complete: { cardClass: "border-success/40 bg-success-subtle/30", iconClass: "text-success", icon: CheckCircle2 },
  running:  { cardClass: "border-brand/40 bg-brand-subtle/30",     iconClass: "text-brand-dark", icon: Loader2, spin: true },
  pending:  { cardClass: "border-line bg-canvas/50",               iconClass: "text-ink-subtle", icon: Clock },
  failed:   { cardClass: "border-danger/40 bg-danger-subtle/30",   iconClass: "text-danger", icon: XCircle },
  blocked:  { cardClass: "border-warning/40 bg-warning-subtle/30", iconClass: "text-warning", icon: GitBranch },
};

const Stat: React.FC<{ items: { label: string; value: number; tone: string }[] }> = ({ items }) => (
  <div className="grid grid-cols-3 gap-2">
    {items.map((it) => (
      <div key={it.label} className="rounded-md bg-canvas px-2 py-2 text-center">
        <div className={cn("text-2xl font-semibold tabular-nums", it.tone)}>{it.value}</div>
        <div className="text-[10.5px] uppercase tracking-wider text-ink-muted">{it.label}</div>
      </div>
    ))}
  </div>
);
