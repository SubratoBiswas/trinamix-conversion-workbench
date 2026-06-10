import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, Search, Database, FileSpreadsheet, Sparkles,
  Save, Play, Plus, GraduationCap, Trash2, Workflow,
  ChevronDown, ChevronRight, ListChecks, BarChart3, Lightbulb,
} from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, LearningApi } from "@/api";
import {
  Button, PageLoader, Pill, Spinner,
} from "@/components/ui/Primitives";
import { ColumnProfileCard } from "@/components/datasets/ColumnProfileCard";
import { RecommendationsPanel } from "@/components/recommendations/RecommendationsPanel";
import { buildRecommendations, type Recommendation } from "@/lib/recommendations";
import { cn } from "@/lib/utils";
import type {
  Conversion,
  DatasetDetail,
  DatasetPreview,
  FBDIField,
  FBDITemplate,
  TemplateSuggestion,
} from "@/types";

interface AppliedStep {
  id: string;
  title: string;
  ruleType?: string;
  column: string;
  learned?: boolean;
  appliedAt: string;
}

const COLUMN_WIDTH = 220;

export const DatasetPreparationPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const dsId = id!;
  const nav = useNavigate();

  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [templateSuggestions, setTemplateSuggestions] = useState<TemplateSuggestion[]>([]);
  const [projects, setProjects] = useState<Conversion[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [targetFields, setTargetFields] = useState<FBDIField[]>([]);
  const [boundProject, setBoundProject] = useState<Conversion | null>(null);

  const [steps, setSteps] = useState<AppliedStep[]>([]);
  const [appliedIds, setAppliedIds] = useState<Set<string>>(new Set());
  const [learnedIds, setLearnedIds] = useState<Set<string>>(new Set());
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());
  const [flash, setFlash] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showFlash = useCallback((msg: string) => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
    setFlash(msg);
    flashTimer.current = setTimeout(() => setFlash(null), 3000);
  }, []);
  const [columnFilter, setColumnFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stepView, setStepView] = useState<"columns" | "steps">("columns");
  const [saving, setSaving] = useState(false);

  // Load dataset + preview + auxiliary data
  useEffect(() => {
    if (!dsId) return;
    DatasetsApi.get(dsId).then(setDataset);
    DatasetsApi.preview(dsId, 200).then(setPreview).catch(() => setPreview({ columns: [], rows: [], total_rows: 0 }));
    DatasetsApi.suggestTemplate(dsId)
      .then(r => setTemplateSuggestions(r.suggestions))
      .catch(() => {});
    ConversionsApi.list().then(setProjects);
    FbdiApi.list().then(setTemplates);
  }, [dsId]);

  // If a project is bound to this dataset, fetch its target fields for context-aware recs
  useEffect(() => {
    const proj = projects.find((p) => p.dataset_id === dsId) || null;
    setBoundProject(proj);
    if (proj && proj.template_id) {
      FbdiApi.fields(proj.template_id).then(setTargetFields);
    }
  }, [projects, dsId]);

  // Compute recommendations from dataset + preview + target fields
  const recommendations = useMemo<Recommendation[]>(() => {
    if (!dataset) return [];
    const all = buildRecommendations({
      dataset,
      preview,
      targetFields: targetFields.length > 0 ? targetFields : undefined,
    });
    return all.filter((r) => !dismissedIds.has(r.id));
  }, [dataset, preview, targetFields, dismissedIds]);

  // ── All useCallback hooks must be declared BEFORE any conditional return ──

  const apply = useCallback(async (rec: Recommendation, learn: boolean) => {
    const step: AppliedStep = {
      id: rec.id,
      title: rec.title,
      ruleType: rec.ruleType,
      column: rec.column,
      learned: learn,
      appliedAt: new Date().toISOString(),
    };
    setSteps((s) => [step, ...s]);
    setAppliedIds((s) => new Set(s).add(rec.id));
    if (learn) {
      setLearnedIds((s) => new Set(s).add(rec.id));
      try {
        await LearningApi.capture({
          kind: "rule",
          category: rec.ruleType || "Custom Rule",
          original_value: rec.column,
          resolved_value: rec.title,
        });
        showFlash(`Rule saved to library: ${rec.title}`);
      } catch {
        showFlash("Step applied — rule could not be saved to library");
      }
    }
  }, [showFlash]);

  const dismiss = useCallback((rec: Recommendation) => {
    setDismissedIds((s) => new Set(s).add(rec.id));
  }, []);

  const saveSteps = useCallback(async () => {
    if (steps.length === 0) { showFlash("No steps to save — apply recommendations first"); return; }
    setSaving(true);
    try {
      const learnedSteps = steps.filter(s => s.learned && s.ruleType);
      if (learnedSteps.length > 0) {
        for (const s of learnedSteps) {
          await LearningApi.capture({
            kind: "rule",
            category: s.ruleType!,
            original_value: s.column,
            resolved_value: s.title,
          });
        }
        showFlash(`${learnedSteps.length} rule(s) saved to Rule Library`);
      } else {
        showFlash(`${steps.length} step(s) logged — use 'Apply & Learn' to save rules permanently`);
      }
    } catch {
      showFlash("Failed to save rules");
    } finally {
      setSaving(false);
    }
  }, [steps, showFlash]);

  const removeStep = useCallback((id: string) => {
    setSteps((s) => s.filter((x) => x.id !== id));
    setAppliedIds((s) => { const n = new Set(s); n.delete(id); return n; });
    setLearnedIds((s) => { const n = new Set(s); n.delete(id); return n; });
  }, []);

  // ── Conditional early return — no more hooks below this line ──
  if (!dataset) return <PageLoader />;

  // Build filtered column list for the left panel
  const columns = (dataset.columns ?? [])
    .filter((c) => !search || c.column_name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => a.position - b.position);

  // Pull values per column from the preview for distribution rendering
  const valuesByColumn = (col: string) =>
    (preview?.rows || []).map((r) => r[col]).filter((v) => v != null && v !== "");

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-canvas">
      {/* Dark header (Oracle-style) */}
      <header className="flex items-center gap-3 border-b border-sidebar/20 bg-sidebar px-5 py-3 text-slate-100">
        <Link to="/datasets" className="flex items-center gap-1.5 rounded p-1 text-slate-300 hover:bg-sidebar-hover">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <Database className="h-4 w-4 text-slate-400" />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <div className="text-sm font-semibold">{dataset.name}</div>
            {dataset.detected_object_type && (
              <span className="inline-flex items-center gap-1 rounded bg-amber-500/20 px-1.5 py-0.5 text-[11px] font-medium text-amber-200">
                <Sparkles className="h-3 w-3" />
                {dataset.detected_object_type} · {Math.round((dataset.detection_confidence ?? 0) * 100)}%
              </span>
            )}
          </div>
          <div className="text-[11px] text-slate-400">
            {dataset.row_count.toLocaleString()} rows · {dataset.column_count} columns · {dataset.file_type.toUpperCase()}
          </div>
        </div>
        {boundProject ? (
          <Link to={`/projects/${boundProject.project_id}`} className="flex items-center gap-2 rounded-md bg-sidebar-hover px-3 py-1.5 text-xs text-slate-200 hover:bg-brand">
            <FileSpreadsheet className="h-3.5 w-3.5" />
            <span>Project: {boundProject.name}</span>
          </Link>
        ) : (
          <button
            onClick={() => nav("/projects/new", { state: { datasetId: dsId } })}
            className="flex items-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
          >
            <Workflow className="h-3.5 w-3.5" /> Add to Dataflow
          </button>
        )}
        <button
          onClick={saveSteps}
          disabled={saving}
          className="flex items-center gap-1.5 rounded-md bg-sidebar-hover px-3 py-1.5 text-xs text-slate-200 hover:bg-brand-dark disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" /> {saving ? "Saving…" : "Save"}
        </button>
      </header>

      {/* Flash toast */}
      {flash && (
        <div className="mx-4 mt-2 rounded-md bg-brand px-4 py-2 text-sm text-white shadow-md">
          {flash}
        </div>
      )}

      {/* Template Suggestions Banner */}
      {templateSuggestions.length > 0 && !boundProject && (
        <div className="mx-4 mt-3 bg-amber-50 border border-amber-200 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-1.5">
            <Lightbulb className="h-4 w-4 text-amber-600" />
            <span className="font-semibold text-amber-800 text-sm">Suggested FBDI Templates</span>
          </div>
          <p className="text-xs text-amber-700 mb-2">
            Based on your file name and columns, these templates are the closest match:
          </p>
          <div className="flex gap-2 flex-wrap">
            {templateSuggestions.map((s) => (
              <div
                key={s.template_id}
                className="flex items-center gap-2 bg-white border border-amber-200 rounded px-3 py-1.5 text-xs"
              >
                <span className="font-medium text-gray-800">{s.template_name}</span>
                <span className="text-gray-400">·</span>
                <span className="text-gray-500">{s.business_object}</span>
                <span
                  className={`font-bold px-1.5 py-0.5 rounded-full ${
                    s.confidence >= 0.7
                      ? "bg-green-100 text-green-700"
                      : s.confidence >= 0.4
                      ? "bg-yellow-100 text-yellow-700"
                      : "bg-gray-100 text-gray-500"
                  }`}
                >
                  {Math.round(s.confidence * 100)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Body — 3-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* LEFT — column / steps panel */}
        <aside className="flex w-[260px] shrink-0 flex-col border-r border-line bg-white">
          {/* Tab strip */}
          <div className="flex border-b border-line">
            <button
              onClick={() => setStepView("columns")}
              className={cn("flex-1 px-3 py-2.5 text-xs font-semibold uppercase tracking-wider transition",
                stepView === "columns" ? "border-b-2 border-brand text-brand-dark" : "text-ink-muted hover:text-ink")}
            >
              <BarChart3 className="mr-1.5 inline h-3 w-3" /> Columns
            </button>
            <button
              onClick={() => setStepView("steps")}
              className={cn("flex-1 px-3 py-2.5 text-xs font-semibold uppercase tracking-wider transition",
                stepView === "steps" ? "border-b-2 border-brand text-brand-dark" : "text-ink-muted hover:text-ink")}
            >
              <ListChecks className="mr-1.5 inline h-3 w-3" /> Steps
              {steps.length > 0 && (
                <span className="ml-1 rounded-full bg-brand px-1.5 py-0.5 text-[10px] text-white">{steps.length}</span>
              )}
            </button>
          </div>

          {stepView === "columns" ? (
            <>
              <div className="border-b border-line p-2.5">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle" />
                  <input
                    className="input !pl-8 !text-xs"
                    placeholder="Search columns…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {columns.map((col) => {
                  const isSelected = columnFilter === col.column_name;
                  const colRecs = recommendations.filter((r) => r.column === col.column_name).length;
                  return (
                    <button
                      key={col.id}
                      onClick={() => setColumnFilter(isSelected ? null : col.column_name)}
                      className={cn(
                        "flex w-full items-center gap-2 border-b border-line/60 px-3 py-2 text-left transition",
                        isSelected ? "bg-brand-subtle" : "hover:bg-canvas",
                      )}
                    >
                      <div className="flex-1 truncate">
                        <div className="text-xs font-medium text-ink">{col.column_name}</div>
                        <div className="font-mono text-[10px] text-ink-muted">
                          {col.inferred_type || "string"} · {col.distinct_count} distinct
                          {col.null_percent > 0 && <span className="text-warning"> · {col.null_percent}% null</span>}
                        </div>
                      </div>
                      {colRecs > 0 && (
                        <span className="rounded-full bg-brand-subtle px-1.5 py-0.5 text-[10px] font-semibold text-brand-dark">
                          {colRecs}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="flex-1 overflow-y-auto p-2.5">
              {steps.length === 0 ? (
                <div className="px-3 py-8 text-center text-xs text-ink-muted">
                  <ListChecks className="mx-auto mb-2 h-6 w-6 text-ink-subtle" />
                  No steps yet.
                  <div className="mt-1">Apply a recommendation to start building the prep pipeline.</div>
                </div>
              ) : (
                <ol className="space-y-1.5">
                  {steps.map((s, i) => (
                    <li key={s.id} className="rounded-md border border-line bg-white px-2.5 py-2 group">
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-subtle text-[10px] font-semibold text-brand-dark">
                          {steps.length - i}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] font-medium text-ink">{s.title}</div>
                          <div className="font-mono text-[10px] text-ink-muted">
                            {s.column}{s.ruleType ? ` · ${s.ruleType}` : ""}
                          </div>
                          {s.learned && (
                            <div className="mt-1 inline-flex items-center gap-1 rounded bg-brand-subtle px-1.5 py-0.5 text-[10px] text-brand-dark">
                              <GraduationCap className="h-2.5 w-2.5" /> Learned
                            </div>
                          )}
                        </div>
                        <button
                          onClick={() => removeStep(s.id)}
                          className="opacity-0 transition group-hover:opacity-100 text-ink-subtle hover:text-danger"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </li>
                  ))}
                  <li className="mt-3 rounded-md border-2 border-dashed border-line bg-canvas px-2.5 py-3 text-center">
                    <div className="text-[11px] font-semibold text-ink">Results</div>
                    <div className="text-[10px] text-ink-muted">All steps combined</div>
                  </li>
                </ol>
              )}
            </div>
          )}
        </aside>

        {/* CENTER — column profiles + data grid */}
        <main className="flex flex-1 flex-col overflow-hidden">
          {/* Sticky profile cards row */}
          <div className="border-b border-line bg-canvas">
            <div className="flex overflow-x-auto">
              {columns.map((col) => {
                const vals = valuesByColumn(col.column_name);
                return (
                  <ColumnProfileCard
                    key={col.id}
                    column={col}
                    values={vals}
                    width={COLUMN_WIDTH}
                    selected={columnFilter === col.column_name}
                    onClick={() => setColumnFilter(columnFilter === col.column_name ? null : col.column_name)}
                  />
                );
              })}
            </div>
          </div>

          {/* Data grid */}
          <div className="flex-1 overflow-auto bg-white">
            {!preview ? <PageLoader label="Loading preview…" /> : (
              <table className="table-shell">
                <thead>
                  <tr>
                    <th className="!sticky !left-0 !z-20 !w-12 !min-w-12 !border-r !border-line !bg-canvas">#</th>
                    {columns.map((col) => (
                      <th
                        key={col.id}
                        style={{ width: COLUMN_WIDTH, minWidth: COLUMN_WIDTH }}
                        className={cn(
                          "!font-mono !text-[10px] !uppercase",
                          columnFilter === col.column_name && "!bg-brand-subtle !text-brand-dark"
                        )}
                      >
                        {col.column_name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, ri) => (
                    <tr key={ri}>
                      <td className="sticky left-0 z-10 border-r border-line bg-canvas font-mono text-[11px] text-ink-subtle">{ri + 1}</td>
                      {columns.map((col) => {
                        const v = row[col.column_name];
                        const isNull = v == null || v === "";
                        return (
                          <td
                            key={col.id}
                            style={{ width: COLUMN_WIDTH, minWidth: COLUMN_WIDTH }}
                            className={cn(
                              "whitespace-nowrap font-mono text-[12px]",
                              isNull ? "italic text-ink-subtle" : "text-ink",
                              columnFilter === col.column_name && "bg-brand-subtle/30"
                            )}
                          >
                            {isNull ? "null" : String(v)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </main>

        {/* RIGHT — recommendations panel */}
        <RecommendationsPanel
          recommendations={recommendations}
          appliedIds={appliedIds}
          learnedIds={learnedIds}
          columnFilter={columnFilter}
          setColumnFilter={setColumnFilter}
          onApply={apply}
          onDismiss={dismiss}
          onAddRule={(r) => {
            if (boundProject) {
              nav(`/transformations?project=${boundProject.id}`);
            } else {
              nav("/projects/new", { state: { datasetId: dsId } });
            }
          }}
          className="w-[360px]"
        />
      </div>
    </div>
  );
};
