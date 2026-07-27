import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, Sparkles, ArrowRight, Check, GraduationCap, Loader2, X, Plus, Layers, Boxes, CheckCircle2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, FusionModulesApi, ProjectsApi, SourceSystemsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { FBDITemplate, FusionModule, Project } from "@/types";

const pct = (n: number) => `${Math.round((n || 0) * 100)}%`;
const confTone = (n: number) => (n >= 0.6 ? "success" : n >= 0.3 ? "warning" : "neutral");

type Item = {
  key: string;
  file: File;
  status: "pending" | "analyzing" | "ready" | "error";
  datasetId?: string;
  learned?: boolean;
  source?: string;
  sourceConf?: number;
  templateId?: string;
  targetConf?: number;
  error?: string;
  dirty?: boolean;   // user changed source/target since last learn
  saving?: boolean;  // learn request in flight
  // Which worksheet of a multi-sheet workbook this row imports. Undefined for
  // single-sheet files. A two-sheet book (e.g. Customer + Address) expands into
  // one row per sheet at analyze time so BOTH sheets' columns become available —
  // previously the parser silently kept only the largest sheet (QA issue #1).
  sheet?: string;
};

// One conversion object type and its ordered FBDI steps (supplier → 6 files).
type ObjType = { key: string; label: string; step_count: number; steps: { label: string; load_order: number }[] };

// Detected target → catalog key (mirrors the backend resolver) so a file
// detected as Supplier expands to its full 6-file set in the preview.
const OBJ_ALIASES: Record<string, string[]> = {
  supplier: ["supplier", "vendor"],
  customer: ["customer", "client"],
  item: ["item", "product", "material"],
  ap_invoice: ["ap invoice", "ap_invoice", "payable"],
  ar_invoice: ["ar invoice", "ar_invoice", "autoinvoice", "receivable"],
  gl_journal: ["gl journal", "gl_journal", "journal"],
};

/**
 * Guided "Convert a file" flow — supports MULTIPLE files:
 *   upload → AI detects each file's source system + target FBDI template
 *   (editable) → create one conversion per file → AI column mapping → FBDI output.
 */
export const ConvertFilePage: React.FC = () => {
  const nav = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [sources, setSources] = useState<{ code: string; display_name: string }[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNewEng, setShowNewEng] = useState(false);
  const [savingEng, setSavingEng] = useState(false);
  const [genKey, setGenKey] = useState<string | null>(null);
  const [genSummary, setGenSummary] = useState<string | null>(null);
  // Fan-out preview: object-type catalog + per-file deselected steps.
  const [objectCatalog, setObjectCatalog] = useState<ObjType[]>([]);
  const [disabledSteps, setDisabledSteps] = useState<Record<string, string[]>>({});
  // Fusion module catalog — optional default (non-file) conversions to also create.
  const [fusionModules, setFusionModules] = useState<FusionModule[]>([]);
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  const toggleModule = (code: string) =>
    setSelectedModules((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  const [engForm, setEngForm] = useState({ name: "", client: "", source_system: "", target_environment: "FBDI File download" });

  useEffect(() => {
    ProjectsApi.list().then((ps) => { setProjects(ps); if (ps[0]) setProjectId(ps[0].id); }).catch(() => {});
    SourceSystemsApi.list().then(setSources).catch(() => setSources([]));
    FbdiApi.list().then(setTemplates).catch(() => setTemplates([]));
    ConversionsApi.objectTypes().then(setObjectCatalog).catch(() => setObjectCatalog([]));
    FusionModulesApi.list().then(setFusionModules).catch(() => setFusionModules([]));
  }, []);

  // Resolve a file's detected target (via its chosen template's business object)
  // to a fan-out object type, and toggle individual steps on/off.
  const resolveObjType = (it: Item): ObjType | null => {
    const tpl = templates.find((t) => t.id === it.templateId);
    const detected = (tpl?.business_object || tpl?.name || "").toLowerCase();
    if (!detected || objectCatalog.length === 0) return null;
    for (const c of objectCatalog) {
      const al = OBJ_ALIASES[c.key] || [c.key];
      if (al.some((a) => detected.includes(a))) return c;
    }
    return null;
  };
  const toggleStep = (key: string, label: string) =>
    setDisabledSteps((prev) => {
      const cur = new Set(prev[key] || []);
      cur.has(label) ? cur.delete(label) : cur.add(label);
      return { ...prev, [key]: Array.from(cur) };
    });

  const addFiles = (fl: FileList | null) => {
    const add = Array.from(fl || []).map((file) => ({
      key: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`,
      file, status: "pending" as const,
    }));
    if (add.length) setItems((prev) => [...prev, ...add]);
  };
  const patch = (key: string, p: Partial<Item>) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...p } : it)));
  const remove = (key: string) => setItems((prev) => prev.filter((it) => it.key !== key));

  // Teach the AI the user-confirmed source + target for this file's column
  // signature, so the next file with the same columns auto-recommends correctly.
  const learnRow = async (it: Item) => {
    if (!it.datasetId || !it.source) return;
    patch(it.key, { saving: true });
    setError(null);
    try {
      const tpl = templates.find((t) => t.id === it.templateId);
      await DatasetsApi.classifyLearn(it.datasetId, {
        source_system: it.source,
        template_id: it.templateId || undefined,
        target_object: tpl?.business_object || tpl?.name || undefined,
      });
      patch(it.key, { saving: false, learned: true, dirty: false });
    } catch (e: any) {
      patch(it.key, { saving: false });
      setError(e?.response?.data?.detail || "Could not save the learned mapping — try again in a moment.");
    }
  };

  const createEngagement = async () => {
    if (!engForm.name.trim()) return;
    setSavingEng(true); setError(null);
    try {
      const created: any = await ProjectsApi.create({
        name: engForm.name.trim(),
        client: engForm.client.trim() || undefined,
        source_system: engForm.source_system || undefined,
        target_environment: engForm.target_environment || undefined,
        status: "planning",
      } as any);
      setProjects((prev) => [created, ...prev]);
      setProjectId(String(created.id));
      setShowNewEng(false);
      setEngForm({ name: "", client: "", source_system: "", target_environment: "FBDI File download" });
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not create engagement.");
    } finally {
      setSavingEng(false);
    }
  };

  const analyzeAll = async () => {
    setAnalyzing(true); setError(null);
    // Expand multi-sheet workbooks FIRST: one row per data sheet, so a
    // Customer + Address book imports both instead of silently keeping only the
    // largest sheet (QA issue #1). Only rows not already tied to a sheet and not
    // yet uploaded are considered.
    let work = items;
    const expanded: Item[] = [];
    for (const it of items) {
      if (it.status === "ready" || it.sheet) { expanded.push(it); continue; }
      if (!/\.(xlsx|xlsm|xls)$/i.test(it.file.name)) { expanded.push(it); continue; }
      try {
        const peek = await DatasetsApi.peekSheets(it.file);
        const real = (peek?.sheets || []).filter((s) => (s.rows || 0) > 1);
        if (real.length > 1) {
          real.forEach((s: any, i: number) =>
            expanded.push({ ...it, key: `${it.key}::${s.name}`, sheet: s.name,
                            status: i === 0 ? it.status : "pending" }));
          continue;
        }
      } catch { /* peek is best-effort — fall through to a normal single upload */ }
      expanded.push(it);
    }
    if (expanded.length !== items.length) { work = expanded; setItems(expanded); }

    for (const it of work) {
      if (it.status === "ready") continue;
      patch(it.key, { status: "analyzing", error: undefined });
      try {
        const baseName = it.file.name.replace(/\.[^.]+$/, "");
        const ds: any = await DatasetsApi.upload(
          it.file, it.sheet ? `${baseName} — ${it.sheet}` : baseName,
          undefined, it.sheet);
        const cls = await DatasetsApi.classify(ds.id);
        patch(it.key, {
          status: "ready", datasetId: ds.id, learned: cls.learned,
          source: cls.source.detected || "custom",
          sourceConf: cls.source.candidates[0]?.confidence ?? 0,
          templateId: cls.target.detected_template_id || "",
          targetConf: cls.target.suggestions[0]?.confidence ?? 0,
        });
      } catch (e: any) {
        patch(it.key, { status: "error", error: e?.response?.data?.detail || "Could not analyze this file." });
      }
    }
    setAnalyzing(false);
  };

  // Req 1: from ONE dataset, generate the full set of FBDI templates the
  // detected conversion object needs (e.g. supplier → 7 templates), each
  // auto-mapped and load-sequenced. Object type is inferred from the detected
  // target template; the backend resolves it (supplier/customer/item/…).
  const generateSetForRow = async (it: Item) => {
    if (!projectId || !it.datasetId) return;
    const tpl = templates.find((t) => t.id === it.templateId);
    const objectType = tpl?.business_object || tpl?.name || "";
    if (!objectType) { setError("Pick a target template first so the object type can be detected."); return; }
    setGenKey(it.key); setError(null); setGenSummary(null);
    try {
      const r = await ConversionsApi.generateSet({
        project_id: projectId, dataset_id: it.datasetId, object_type: objectType,
      });
      const parts = [`${r.created.length} created`];
      if (r.existing?.length) parts.push(`${r.existing.length} already present`);
      if (r.missing?.length) parts.push(`${r.missing.length} missing template${r.missing.length === 1 ? "" : "s"} (${r.missing.map((m) => m.label).join(", ")})`);
      setGenSummary(`${r.object_type}: ${parts.join(" · ")}. Opening the engagement…`);
      setTimeout(() => nav(`/projects/${projectId}`), 1400);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not generate the FBDI template set.");
    } finally {
      setGenKey(null);
    }
  };

  const createAll = async () => {
    const ready = items.filter((it) => it.status === "ready" && it.templateId && it.datasetId);
    if (!projectId || (ready.length === 0 && selectedModules.length === 0)) return;
    setCreating(true); setError(null);
    const proj = projects.find((p) => p.id === projectId);
    const outputMode = /fbdi/i.test((proj as any)?.target_environment || "") ? "fbdi_download" : "fusion_load";
    try {
      for (const it of ready) {
        const tpl = templates.find((t) => t.id === it.templateId);
        const targetObject = tpl?.business_object || tpl?.name;
        await DatasetsApi.classifyLearn(it.datasetId!, {
          source_system: it.source, template_id: it.templateId, target_object: targetObject || undefined,
        }).catch(() => {});
        // For a multi-file conversion object (supplier/customer/item/…), fan out
        // the FULL FBDI set from this one dataset — so the engagement gets the
        // whole load sequence starting at step 1 (e.g. Supplier Import), not just
        // whichever single file the AI happened to detect. Falls back to a single
        // conversion only when the object type isn't a known set.
        let fannedOut = false;
        try {
          const r = await ConversionsApi.generateSet({
            project_id: projectId, dataset_id: it.datasetId!, object_type: targetObject || "",
          });
          fannedOut = (r.created?.length || 0) + (r.existing?.length || 0) > 0;
          // Honor any steps the user unticked in the preview: drop them.
          const off = new Set(disabledSteps[it.key] || []);
          if (off.size) {
            for (const c of r.created || []) {
              if (off.has(c.label) && c.conversion_id) {
                await ConversionsApi.remove(c.conversion_id).catch(() => {});
              }
            }
          }
        } catch { /* not a known object set — fall through to single create */ }
        if (!fannedOut) {
          await ConversionsApi.create({
            project_id: projectId,
            name: it.file.name.replace(/\.[^.]+$/, ""),
            dataset_id: it.datasetId, template_id: it.templateId,
            target_object: targetObject, source_type: "dataset",
            output_mode: outputMode, status: "draft",
          } as any);
        }
      }
      // Also auto-create the default planned conversions for any selected Fusion
      // modules (the module catalog below) — same as the setup wizard scope step.
      if (selectedModules.length) {
        await ProjectsApi.autoPopulate(projectId, selectedModules).catch(() => {});
      }
      nav(`/projects/${projectId}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not create conversions.");
      setCreating(false);
    }
  };

  const readyCount = items.filter((it) => it.status === "ready" && it.templateId).length;
  const analyzed = items.some((it) => it.status === "ready");

  return (
    <>
      <PageTitle
        title="Convert files"
        subtitle="Upload one or more source extracts — AI detects each file's source system and target Fusion FBDI template, then hands off to mapping and FBDI download."
      />

      {(() => {
        // Wizard-style progress stepper for the file-upload flow.
        const curStep = creating ? 4 : analyzed ? 3 : items.length ? 2 : 1;
        const steps = [
          { n: 1, label: "Upload files", icon: UploadCloud },
          { n: 2, label: "Detect source & target", icon: Sparkles },
          { n: 3, label: "Review & scope", icon: Layers },
          { n: 4, label: "Create & map", icon: Check },
        ];
        return (
          <ol className="mb-4 flex items-center gap-2 rounded-lg border border-line bg-white p-3">
            {steps.map((s, i) => {
              const Icon = s.icon;
              const active = curStep === s.n;
              const done = curStep > s.n;
              return (
                <React.Fragment key={s.n}>
                  <li className={cn(
                    "flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium",
                    active && "bg-brand-subtle text-brand-dark",
                    done && "text-ink",
                    !active && !done && "text-ink-muted",
                  )}>
                    <span className={cn(
                      "flex h-5 w-5 items-center justify-center rounded-full text-[10.5px] font-semibold",
                      active && "bg-brand text-white",
                      done && "bg-success text-white",
                      !active && !done && "bg-canvas text-ink-muted",
                    )}>
                      {done ? <CheckCircle2 className="h-3 w-3" /> : <Icon className="h-3 w-3" />}
                    </span>
                    {s.label}
                  </li>
                  {i < steps.length - 1 && (
                    <span className={cn("h-px flex-1", curStep > s.n ? "bg-success" : "bg-line")} />
                  )}
                </React.Fragment>
              );
            })}
          </ol>
        );
      })()}

      {error && (
        <div className="mb-4 rounded-md border border-danger/40 bg-danger-subtle/50 px-4 py-3 text-[12.5px] text-danger">{error}</div>
      )}
      {genSummary && (
        <div className="mb-4 rounded-md border border-success/40 bg-success-subtle/50 px-4 py-3 text-[12.5px] text-ink">
          <Layers className="mr-1 inline h-3.5 w-3.5 text-success" /> {genSummary}
        </div>
      )}

      <Card className="mb-4">
        <CardBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <div className="flex items-center justify-between">
                <label className="label">Engagement</label>
                <button
                  type="button"
                  onClick={() => setShowNewEng(true)}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-brand hover:text-brand-dark"
                >
                  <Plus className="h-3 w-3" /> New engagement
                </button>
              </div>
              <select className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.length === 0 && <option value="">No engagements yet — create one →</option>}
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` · ${p.client}` : ""}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Source files</label>
              <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-line bg-canvas px-4 py-2.5 hover:border-brand">
                <UploadCloud className="h-5 w-5 text-brand" />
                <span className="text-sm text-ink">Add CSV / XLSX files (multiple allowed)</span>
                <input type="file" multiple accept=".csv,.xlsx,.xls" className="hidden"
                  onChange={(e) => { addFiles(e.target.files); e.currentTarget.value = ""; }} />
              </label>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={analyzeAll} loading={analyzing} disabled={items.length === 0 || !projectId}>
              <Sparkles className="h-4 w-4" /> Upload & analyze {items.length > 0 ? `(${items.length})` : ""}
            </Button>
            {(readyCount > 0 || selectedModules.length > 0) && (
              <Button variant="primary" onClick={createAll} loading={creating}>
                <Check className="h-4 w-4" />
                {readyCount > 0
                  ? ` Create ${readyCount} conversion${readyCount === 1 ? "" : "s"} & map`
                  : ` Create ${selectedModules.length} module set${selectedModules.length === 1 ? "" : "s"}`}
              </Button>
            )}
          </div>
        </CardBody>
      </Card>

      {items.length > 0 && (
        <Card>
          <CardHeader title="Files" subtitle="AI classification — override the source or target per file if needed" />
          <div className="overflow-x-auto">
            <table className="table-shell">
              <thead>
                <tr><th>File</th><th>Source system</th><th>Target FBDI template</th><th>Status</th><th></th></tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.key}>
                    <td className="max-w-[220px] truncate font-medium text-ink"
                        title={it.sheet ? `${it.file.name} — sheet "${it.sheet}"` : it.file.name}>
                      {it.file.name}
                      {it.sheet && (
                        <span className="ml-1.5 rounded bg-canvas px-1 py-0.5 font-mono text-[9px] font-normal text-ink-subtle">
                          {it.sheet}
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <select className="input !h-8 !w-auto !text-xs" disabled={it.status !== "ready"}
                          value={it.source || ""} onChange={(e) => patch(it.key, { source: e.target.value, dirty: true, learned: false })}>
                          <option value="">—</option>
                          {sources.map((s) => <option key={s.code} value={s.code}>{s.display_name}</option>)}
                        </select>
                        {it.status === "ready" && it.sourceConf != null && (
                          <Pill tone={confTone(it.sourceConf)}>AI {pct(it.sourceConf)}</Pill>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <select className="input !h-8 !w-auto !text-xs" disabled={it.status !== "ready"}
                          value={it.templateId || ""} onChange={(e) => patch(it.key, { templateId: e.target.value, dirty: true, learned: false })}>
                          <option value="">— choose —</option>
                          {templates.map((t) => <option key={t.id} value={t.id}>{t.business_object || t.name}</option>)}
                        </select>
                        {it.status === "ready" && it.targetConf != null && (
                          <Pill tone={confTone(it.targetConf)}>AI {pct(it.targetConf)}</Pill>
                        )}
                      </div>
                    </td>
                    <td>
                      {it.status === "analyzing" ? <span className="inline-flex items-center gap-1 text-[11px] text-ink-muted"><Loader2 className="h-3 w-3 animate-spin" /> analyzing</span>
                        : it.status === "ready" ? <Pill tone="success">{it.learned ? <><GraduationCap className="mr-1 h-3 w-3" />learned</> : "ready"}</Pill>
                        : it.status === "error" ? <Pill tone="danger" >error</Pill>
                        : <span className="text-[11px] text-ink-subtle">pending</span>}
                      {it.error && <div className="mt-0.5 max-w-[180px] truncate text-[10.5px] text-danger" title={it.error}>{it.error}</div>}
                    </td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => generateSetForRow(it)}
                          disabled={it.status !== "ready" || !it.templateId || genKey === it.key}
                          title="Generate ALL related FBDI templates for this object (e.g. supplier → 7 files), each auto-mapped and load-sequenced"
                          className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-brand-dark transition hover:bg-brand-subtle disabled:opacity-40"
                        >
                          {genKey === it.key ? <Loader2 className="h-3 w-3 animate-spin" /> : <Layers className="h-3 w-3" />}
                          {genKey === it.key ? "Generating" : "Generate set"}
                        </button>
                        <button
                          onClick={() => learnRow(it)}
                          disabled={it.status !== "ready" || it.saving || !it.source}
                          title="Teach the AI this source/target — files with the same columns will auto-fill next time"
                          className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition disabled:opacity-40 ${
                            it.dirty ? "bg-brand-subtle text-brand-dark hover:brightness-95"
                            : "text-ink-subtle hover:bg-canvas hover:text-ink"
                          }`}
                        >
                          {it.saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <GraduationCap className="h-3 w-3" />}
                          {it.saving ? "Saving" : it.learned && !it.dirty ? "Learned" : "Learn"}
                        </button>
                        <button onClick={() => remove(it.key)} className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger" title="Remove">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <CardBody className="!pt-3">
            <p className="text-[11px] text-ink-subtle">
              Adjust the source or target, then click <span className="font-medium text-ink">Learn</span> to teach the AI — files with the same columns auto-fill next time. (Creating conversions also saves your choices.) After creating, map each conversion and download its FBDI file from Output Preview to upload into Fusion.
            </p>
          </CardBody>
        </Card>
      )}

      {/* Conversions from your files — a file detected as a multi-file object
          (e.g. Supplier) expands to its full FBDI set, all selected by default;
          untick any step you don't need. Created via generate-set on submit. */}
      {(() => {
        const ready = items.filter((it) => it.status === "ready" && it.templateId);
        const totalConvs = ready.reduce((sum, it) => {
          const steps = resolveObjType(it)?.steps || [];
          const off = new Set(disabledSteps[it.key] || []);
          return sum + (steps.length ? steps.filter((s) => !off.has(s.label)).length : 1);
        }, 0);
        if (!ready.length) return null;
        return (
          <Card className="mt-4">
            <CardHeader
              title={`Conversions from your files (${totalConvs})`}
              subtitle="Objects like Supplier load as a set of FBDI files (Import → Address → Site → Site Assignment → Contacts → Banks) — all selected by default. Untick any step you don't need; you can also change targets above."
            />
            <CardBody className="space-y-2.5">
              {ready.map((it) => {
                const ot = resolveObjType(it);
                const steps = ot?.steps || [];
                const off = new Set(disabledSteps[it.key] || []);
                const tpl = templates.find((t) => t.id === it.templateId);
                const label = ot?.label || tpl?.business_object || tpl?.name || "—";
                return (
                  <div key={it.key} className="rounded-md border border-line bg-white">
                    <div className="flex items-center gap-2 px-2.5 py-1.5 text-[12px]">
                      <span className="min-w-0 flex-1 truncate text-ink" title={it.file.name}>{it.file.name}</span>
                      <ArrowRight className="h-3 w-3 shrink-0 text-ink-subtle" />
                      <span className="font-medium text-ink">{label}</span>
                      <Pill tone="success">FBDI</Pill>
                    </div>
                    {steps.length > 1 && (
                      <div className="border-t border-brand/25 bg-brand-subtle/15 px-2.5 py-2">
                        <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
                          <Layers className="h-3 w-3" />
                          {steps.filter((s) => !off.has(s.label)).length} of {steps.length} {ot?.label} FBDI files selected
                        </div>
                        <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                          {steps.map((s) => {
                            const on = !off.has(s.label);
                            return (
                              <label
                                key={s.label}
                                className={`flex cursor-pointer items-center gap-2 rounded border px-2 py-1 text-[11.5px] transition ${
                                  on ? "border-brand/40 bg-white text-ink" : "border-line bg-canvas text-ink-muted"
                                }`}
                              >
                                <input type="checkbox" checked={on} onChange={() => toggleStep(it.key, s.label)} className="h-3.5 w-3.5 accent-brand" />
                                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand/10 font-mono text-[9px] text-brand-dark">{s.load_order}</span>
                                <span className="truncate">{s.label}</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </CardBody>
          </Card>
        );
      })()}

      {/* Implementation scope · Fusion Cloud modules — optional default (non-file)
          conversions. Picking modules auto-creates one planned conversion per
          canonical Fusion target object on create (same as the setup wizard). */}
      {analyzed && fusionModules.length > 0 && (() => {
        const scoped = fusionModules.filter((m) => selectedModules.includes(m.code));
        // De-duplicated, load-ordered list of the default conversions that will
        // be auto-created from the selected modules (shown as a preview table).
        const objMap = new Map<string, { label: string; planned: number; module: string }>();
        scoped.forEach((m) => m.objects.forEach((o) => {
          if (!objMap.has(o.target_object)) {
            objMap.set(o.target_object, { label: o.label, planned: o.planned_load_order, module: m.name });
          }
        }));
        const objRows = [...objMap.values()].sort((a, b) => a.planned - b.planned);
        return (
          <Card className="mt-4">
            <CardHeader
              title="Implementation scope · Fusion Cloud modules"
              subtitle="Optional — pick the Fusion modules in scope to also auto-create one planned conversion per canonical target object (with load order). These are in addition to your file-based conversions above; you can add / remove them later on the Project Overview."
            />
            <CardBody>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {fusionModules.map((m) => {
                  const on = selectedModules.includes(m.code);
                  return (
                    <button
                      key={m.code}
                      onClick={() => toggleModule(m.code)}
                      className={cn(
                        "rounded-md border bg-white p-3 text-left transition",
                        on ? "border-brand ring-2 ring-brand/20" : "border-line hover:border-brand-dark/40 hover:shadow-soft",
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
                          <Boxes className="h-3.5 w-3.5 text-brand-dark" />
                          {m.name}
                        </span>
                        <Pill tone={on ? "brand" : "neutral"} className="!text-[10px]">
                          {m.objects.length} object{m.objects.length === 1 ? "" : "s"}
                        </Pill>
                      </div>
                      <div className="mt-1 text-[11.5px] text-ink-muted">{m.description}</div>
                      <div className="mt-1.5 font-mono text-[10.5px] text-ink-muted">
                        {m.objects.slice(0, 5).map((o) => o.target_object).join(" · ")}
                        {m.objects.length > 5 && ` · +${m.objects.length - 5} more`}
                      </div>
                    </button>
                  );
                })}
              </div>
              {objRows.length > 0 && (
                <div className="mt-3 rounded-md border border-brand/30 bg-brand-subtle/15 p-3">
                  <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
                    {objRows.length} default conversion{objRows.length === 1 ? "" : "s"} will be auto-created on Create
                  </div>
                  <table className="w-full text-[11.5px]">
                    <thead className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
                      <tr>
                        <th className="pb-1 pr-2">Object</th>
                        <th className="pb-1 pr-2">Load order</th>
                        <th className="pb-1">Module</th>
                      </tr>
                    </thead>
                    <tbody>
                      {objRows.map((o) => (
                        <tr key={o.label} className="border-t border-line/60">
                          <td className="py-1 pr-2 font-medium text-ink">{o.label}</td>
                          <td className="py-1 pr-2 font-mono text-ink-muted">{o.planned}</td>
                          <td className="py-1 text-ink-muted">{o.module}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardBody>
          </Card>
        );
      })()}

      {showNewEng && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => !savingEng && setShowNewEng(false)}
        >
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-lg" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink">New engagement</h3>
              <button onClick={() => !savingEng && setShowNewEng(false)} className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-ink" title="Close">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="label">Engagement name *</label>
                <input
                  className="input" autoFocus value={engForm.name}
                  onChange={(e) => setEngForm({ ...engForm, name: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter") createEngagement(); }}
                  placeholder="e.g. Phoenix Fusion Cutover"
                />
              </div>
              <div>
                <label className="label">Client</label>
                <input
                  className="input" value={engForm.client}
                  onChange={(e) => setEngForm({ ...engForm, client: e.target.value })}
                  placeholder="e.g. Phoenix Corp"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label">Source system</label>
                  <select className="input" value={engForm.source_system} onChange={(e) => setEngForm({ ...engForm, source_system: e.target.value })}>
                    <option value="">—</option>
                    {sources.map((s) => <option key={s.code} value={s.code}>{s.display_name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label">Target</label>
                  <select className="input" value={engForm.target_environment} onChange={(e) => setEngForm({ ...engForm, target_environment: e.target.value })}>
                    <option>FBDI File download</option>
                    <option>Oracle Fusion SCM Cloud</option>
                    <option>Oracle Fusion ERP Cloud</option>
                    <option>Oracle Fusion HCM Cloud</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowNewEng(false)} disabled={savingEng}>Cancel</Button>
              <Button onClick={createEngagement} loading={savingEng} disabled={!engForm.name.trim()}>
                <Check className="h-4 w-4" /> Create engagement
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
