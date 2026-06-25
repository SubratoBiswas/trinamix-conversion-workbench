import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Sparkles, Check, X, RefreshCw, Search, Filter as FilterIcon,
  GraduationCap, Edit2, ArrowLeft, ArrowLeftRight, AlertTriangle, ChevronDown, Lock,
} from "lucide-react";

// P3 — tiny lock glyph for source-column PII badges in the canvas list.
const PiiLockGlyph: React.FC = () => <Lock className="h-2 w-2" />;
import { ConversionsApi, DatasetsApi, FbdiApi, InheritedStandardsApi, LearningApi, MappingApi } from "@/api";
import type { InheritedStandard } from "@/api";
import { RuleAuthorModal } from "@/components/transforms/RuleAuthorModal";
import {
  Button, Card, CardBody, EmptyState, PageLoader, PageTitle, Pill, Spinner,
} from "@/components/ui/Primitives";
import { RecommendationsPanel } from "@/components/recommendations/RecommendationsPanel";
import { buildRecommendations, type Recommendation } from "@/lib/recommendations";
import { confidenceTone, cn, formatNumber, statusTone } from "@/lib/utils";
import type {
  Conversion,
  DatasetColumnProfile,
  DatasetDetail,
  FBDIField,
  MappingSuggestion,
} from "@/types";

type FilterMode = "all" | "required" | "review" | "approved" | "unmapped" | "kb";

// Source-system display labels mirroring the server-driven enum, so the
// KB badge can read "🧠 from NetSuite KB" instead of "🧠 from netsuite KB".
const KB_SOURCE_DISPLAY: Record<string, string> = {
  netsuite: "NetSuite",
  oracle_ebs: "Oracle EBS",
  sap_ecc: "SAP ECC",
  sap_s4: "SAP S/4",
  workday: "Workday",
  jde: "JDE",
  custom: "Custom",
};

export const MappingReviewPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");

  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<string | null>(projParam ?? null);

  const [project, setProject] = useState<Conversion | null>(null);
  const [loadingConversion, setLoadingConversion] = useState(true);
  // Surfaced when the conversion context fails to load (e.g. backend cold-start
  // or redeploy) so the page shows a retry affordance instead of spinning forever.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  // Unified source-column list for the canvas. In dataset mode these are the
  // uploaded file's profiled columns; in EBS live mode they come from
  // Oracle ALL_TAB_COLUMNS for the conversion's ebs_table_hint.
  const [sourceColumns, setSourceColumns] = useState<DatasetColumnProfile[]>([]);
  const [ebsTable, setEbsTable] = useState<string | null>(null);
  // Diagnostic returned by the source-columns endpoint in EBS mode — explains
  // why zero columns came back (no connection / JDBC error / table not found).
  const [ebsDebug, setEbsDebug] = useState<Record<string, any> | null>(null);
  // Target-field ids that have at least one saved transformation rule, so the
  // canvas can badge them (rules apply at Generate Output, not as map lines).
  const [ruleTargetIds, setRuleTargetIds] = useState<Set<number>>(new Set());
  const [targetFields, setTargetFields] = useState<FBDIField[]>([]);
  const [mappings, setMappings] = useState<MappingSuggestion[]>([]);
  // Cascade visibility — when an upstream master has taught a rule
  // (e.g. REMOVE_HYPHEN on Item.InventoryItemNumber), the matching FK
  // columns on this conversion inherit that rule at output time. We
  // surface them as a banner + per-row chips so the analyst can see
  // the propagation without having to open Output Preview.
  const [inherited, setInherited] = useState<InheritedStandard[]>([]);

  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<FilterMode>("all");
  const [search, setSearch] = useState("");
  const [selectedMappingId, setSelectedMappingId] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showRecs, setShowRecs] = useState(true);
  const [appliedRecIds, setAppliedRecIds] = useState<Set<string>>(new Set());
  const [learnedRecIds, setLearnedRecIds] = useState<Set<string>>(new Set());
  const [dismissedRecIds, setDismissedRecIds] = useState<Set<string>>(new Set());

  // Track which source columns have been highlighted in the canvas
  const [hoveredSource, setHoveredSource] = useState<string | null>(null);
  const [hoveredTarget, setHoveredTarget] = useState<number | null>(null);

  // Custom-rule authoring state — opens the universal RuleAuthor modal
  // pre-bound to the inspected mapping.
  const [ruleAuthorOpen, setRuleAuthorOpen] = useState(false);
  const [ruleAuthorMapping, setRuleAuthorMapping] = useState<MappingSuggestion | null>(null);

  // Load projects on mount
  useEffect(() => {
    ConversionsApi.list().then((ps) => {
      setProjects(ps);
      if (!pid && ps[0]) {
        setPid(ps[0].id);
        setParams({ conversion: String(ps[0].id) });
      }
    });
  }, []);

  // Load project context. Supports both source modes:
  //   • dataset mode  → dataset_id set; columns come from the upload
  //   • EBS live mode → dataset_id null; columns stream from Oracle EBS
  // dataset_id presence (not source_type) decides the mode, mirroring the
  // Conversion Detail page's source card rule.
  const loadAll = async () => {
    if (!pid) return;
    setLoadingConversion(true);
    setLoadError(null);
    setMappings([]);
    let proj: Conversion;
    try {
      proj = await ConversionsApi.get(pid);
    } catch (e: any) {
      // Most often a transient backend cold-start / redeploy. Don't strand the
      // user on an infinite spinner — surface it with a Retry.
      const code = e?.response?.status;
      setLoadError(
        code === 404
          ? "This conversion could not be found. It may have been removed, or the backend is still starting up."
          : `Couldn't load this conversion (${code || "network error"}). The backend may be waking up — retry in a moment.`
      );
      setLoadingConversion(false);
      return;
    }
    setProject(proj);

    const isEbs = !proj.dataset_id;

    // A target FBDI template is required in both modes — without it there are
    // no fields to map against. Dataset mode additionally needs a linked file.
    if (!proj.template_id || (!isEbs && !proj.dataset_id)) {
      setDataset(null);
      setSourceColumns([]);
      setEbsTable(null);
      setEbsDebug(null);
      setRuleTargetIds(new Set());
      setTargetFields([]);
      setLoadingConversion(false);
      return;
    }

    try {
      const [fields, ms, std, src] = await Promise.all([
        FbdiApi.fields(proj.template_id),
        MappingApi.list(pid),
        InheritedStandardsApi.forConversion(pid).catch(() => [] as InheritedStandard[]),
        ConversionsApi.sourceColumns(pid).catch(() => ({ source_type: "", table: null, columns: [] as DatasetColumnProfile[] })),
      ]);

      // Dataset detail still drives the Recommendations panel (column-level
      // cleansing). EBS mode has no file, so recommendations are skipped.
      let ds: DatasetDetail | null = null;
      if (!isEbs && proj.dataset_id) {
        ds = await DatasetsApi.get(proj.dataset_id).catch(() => null);
      }

      setDataset(ds);
      setSourceColumns(src.columns || []);
      setEbsTable(src.table ?? proj.ebs_table_hint ?? null);
      setEbsDebug((src as any).debug ?? null);
      setTargetFields(fields);
      setMappings(ms);
      setInherited(std);
    } catch (e: any) {
      setLoadError(`Couldn't load mapping data (${e?.response?.status || "network error"}). Retry in a moment.`);
      setLoadingConversion(false);
      return;
    }
    setLoadingConversion(false);

    // Saved transformation rules → badge their target fields.
    MappingApi.rules(pid)
      .then((rs) => setRuleTargetIds(new Set(rs.filter((r) => r.target_field_id != null).map((r) => r.target_field_id as any))))
      .catch(() => setRuleTargetIds(new Set()));
  };
  useEffect(() => { loadAll(); }, [pid]);

  // Seed standard Oracle Fusion fields for templates with 0 parsed fields
  const [seeding, setSeeding] = useState(false);
  const seedFields = async () => {
    if (!project?.template_id) return;
    setSeeding(true);
    try {
      const res = await FbdiApi.seedStandardFields(project.template_id);
      flash(res.message);
      if (res.seeded > 0) {
        // Reload target fields then re-run mapping
        const fields = await FbdiApi.fields(project.template_id);
        setTargetFields(fields);
        await suggest();
      }
    } catch (err: any) {
      flash(err?.response?.data?.detail ?? "Seed failed — template name not recognised");
    } finally {
      setSeeding(false);
    }
  };

  // Run AI mapping
  const suggest = async () => {
    if (!pid) return;
    setRunning(true);
    try {
      const res = await MappingApi.suggest(pid);
      setMappings(res);
      const auto = res.filter((m) => m.approved_by === "learning-engine").length;
      const kb = res.filter((m) => !!m.kb_source && m.status === "suggested").length;
      const ai = res.filter(
        (m) => m.source_column && m.status === "suggested" && !m.kb_source,
      ).length;
      // Three-part breakdown so the analyst sees where each row came from.
      const parts: string[] = [];
      if (kb)   parts.push(`${kb} pre-filled from Knowledge Bank`);
      if (auto) parts.push(`${auto} auto-applied (same project)`);
      if (ai)   parts.push(`${ai} AI-suggested`);
      flash(parts.length ? `AI mapping run — ${parts.join(", ")}` : "AI mapping run complete");
    } finally { setRunning(false); }
  };

  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 2400); };

  // ── Filtering ──
  const visibleMappings = useMemo(() => {
    return mappings.filter((m) => {
      const term = search.toLowerCase();
      if (term && !((m.target_field_name || "") + " " + (m.source_column || ""))
            .toLowerCase().includes(term)) return false;
      switch (filter) {
        case "required": return m.target_required;
        case "review":   return Boolean(m.review_required);
        case "approved": return m.status === "approved";
        case "unmapped": return !m.source_column && m.target_required;
        case "kb":       return !!m.kb_source;
        default: return true;
      }
    });
  }, [mappings, search, filter]);

  const visibleTargetIds = useMemo(() => new Set(visibleMappings.map((m) => m.target_field_id)), [visibleMappings]);

  // ── Stats — scoped to the active FBDI template's fields only ──
  const stats = useMemo(() => {
    const activeIds = new Set(targetFields.map((f) => f.id));
    const scoped = mappings.filter((m) => activeIds.has(m.target_field_id));
    const total = targetFields.length;
    const mapped = scoped.filter((m) => m.source_column).length;
    const approved = scoped.filter((m) => m.status === "approved").length;
    const reqMissing = scoped.filter((m) => m.target_required && !m.source_column && m.status !== "approved").length;
    const learned = scoped.filter(
      (m) => m.status === "approved" &&
        (m.approved_by === "learning-engine" || m.comment?.includes("[learned]"))
    ).length;
    const kb = scoped.filter((m) => !!m.kb_source).length;
    return { total, mapped, approved, reqMissing, learned, kb };
  }, [mappings, targetFields]);

  // ── Recommendations (column-level cleansing tied to this project) ──
  const recommendations = useMemo<Recommendation[]>(() => {
    if (!dataset) return [];
    return buildRecommendations({ dataset, targetFields });
  }, [dataset, targetFields]);

  const selectedMapping = mappings.find((m) => m.id === selectedMappingId) || null;

  // Every approve teaches — backend persists a LearnedMapping so the next
  // file dropped on the same business object auto-corrects without asking.
  const approve = async (m: MappingSuggestion) => {
    await MappingApi.update(m.id, { status: "approved" });
    flash("Approved & learned — will auto-apply next time");
    loadAll();
  };

  const reject = async (m: MappingSuggestion) => {
    await MappingApi.update(m.id, { status: "rejected" });
    flash("Rejected");
    loadAll();
  };

  const override = async (m: MappingSuggestion, newSourceColumn: string) => {
    await MappingApi.update(m.id, { source_column: newSourceColumn, status: "overridden" });
    flash("Override saved");
    loadAll();
  };

  // Drag-to-map: dropping a source column onto a target field binds it. We
  // update that target's existing suggestion row (created by AI mapping) and
  // mark it "overridden" so it reads as a deliberate manual mapping.
  const mapDrop = async (targetFieldId: number, sourceColumn: string) => {
    const m = mappings.find((x) => x.target_field_id === targetFieldId);
    if (!m) {
      flash("Run AI Mapping first so the field is ready to map");
      return;
    }
    if (m.source_column === sourceColumn) return;
    try {
      await MappingApi.update(m.id, { source_column: sourceColumn, status: "overridden" });
      flash(`Mapped ${sourceColumn} → ${m.target_field_name || "field"}`);
      loadAll();
    } catch (e: any) {
      flash(`Could not map: ${e?.response?.data?.detail || e?.message || "failed"}`);
    }
  };

  // Delete a mapping arrow — clears the source binding and returns the row to
  // an unmapped "suggested" state (the field can then be re-mapped by drag).
  const unmap = async (m: MappingSuggestion) => {
    try {
      await MappingApi.update(m.id, { source_column: null as any, status: "suggested" });
      flash(`Cleared mapping for ${m.target_field_name || "field"}`);
      loadAll();
    } catch (e: any) {
      flash(`Could not clear: ${e?.response?.data?.detail || e?.message || "failed"}`);
    }
  };

  // ── Apply & (optionally) Learn a recommendation ──
  const applyRecommendation = async (rec: Recommendation, learn: boolean) => {
    if (!pid || !rec.ruleType) {
      flash("No transformation rule available for this recommendation");
      return;
    }

    try {
      // 1. Add transformation rule to this conversion
      await MappingApi.addRule(pid, {
        source_column: rec.column,
        rule_type: rec.ruleType,
        rule_config: rec.ruleConfig ?? {},
        description: rec.title,
        ...(rec.targetField
          ? { target_field_id: targetFields.find((f) => f.field_name === rec.targetField)?.id?.toString() }
          : {}),
      });

      // 2. Approve the mapping for this source column (if one exists)
      const matchingMapping = mappings.find((m) => m.source_column === rec.column);
      if (matchingMapping && matchingMapping.status !== "approved") {
        await MappingApi.update(matchingMapping.id, { status: "approved" });
      }

      // 3. On "Apply & Learn" — capture to Learning Center + propagate to all other conversions
      if (learn) {
        // Capture to Learning Center (Rule Library)
        await LearningApi.capture({
          kind: "transformation_rule",
          category: rec.kind,
          original_value: rec.column,
          resolved_value: rec.ruleType,
          rule_type: rec.ruleType,
          rule_config: rec.ruleConfig ?? {},
          target_field: rec.targetField,
          captured_from: rec.title,
          project_id: pid,
          records_auto_fixed: rec.impact.records,
        });

        // Propagate rule to all other conversions that have the same source column
        const allConversions = await ConversionsApi.list();
        const others = allConversions.filter((c) => c.id !== pid);
        await Promise.allSettled(
          others.map(async (conv) => {
            const theirMappings = await MappingApi.list(conv.id);
            const hasColumn = theirMappings.some((m) => m.source_column === rec.column);
            if (!hasColumn) return;
            await MappingApi.addRule(conv.id, {
              source_column: rec.column,
              rule_type: rec.ruleType!,
              rule_config: rec.ruleConfig ?? {},
              description: `[learned] ${rec.title}`,
            });
            const theirMapping = theirMappings.find((m) => m.source_column === rec.column);
            if (theirMapping && theirMapping.status !== "approved") {
              await MappingApi.update(theirMapping.id, { status: "approved" });
            }
          })
        );

        setLearnedRecIds((prev) => new Set([...prev, rec.id]));
        flash(`Applied & learned — rule propagated to all conversions with ${rec.column}`);
      } else {
        setAppliedRecIds((prev) => new Set([...prev, rec.id]));
        flash(`Applied — ${rec.title}`);
      }

      // Dismiss the card from the list after a brief "Applied" display
      setTimeout(() => {
        setDismissedRecIds((prev) => new Set([...prev, rec.id]));
      }, 1500);

      // Refresh mappings so the canvas reflects the approve
      loadAll();
    } catch (err) {
      flash("Failed to apply recommendation");
      console.error(err);
    }
  };

  if (loadingConversion) return <PageLoader />;
  if (loadError) return (
    <div className="p-6">
      <EmptyState
        icon={<AlertTriangle className="h-5 w-5" />}
        title="Couldn't load this conversion"
        description={loadError}
      />
      <div className="mt-3 flex justify-center">
        <Button variant="primary" onClick={() => loadAll()}>
          <RefreshCw className="h-4 w-4" /> Retry
        </Button>
      </div>
    </div>
  );
  if (!pid || !project) return <PageLoader />;

  const isEbs = !project.dataset_id;

  // No target template → nothing to map against, in either mode.
  if (!project.template_id) return (
    <div className="p-6">
      <EmptyState
        icon={<ArrowLeftRight className="h-5 w-5" />}
        title="No FBDI template linked"
        description="Link a target FBDI template to this conversion to begin mapping."
      />
    </div>
  );
  // Dataset mode with no uploaded file → prompt for a source extract.
  if (!isEbs && !dataset) return (
    <div className="p-6">
      <EmptyState
        icon={<ArrowLeftRight className="h-5 w-5" />}
        title="No source file linked yet"
        description="Upload a source extract and link it to this conversion to begin mapping."
      />
    </div>
  );

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-canvas">
      {/* Top bar */}
      <header className="border-b border-line bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <Link
            to={`/projects/${project.project_id}`}
            className="btn-ghost !h-8 shrink-0"
            title="Back to this project's conversions"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span className="ml-1 text-xs">Conversions</span>
          </Link>
          <ArrowLeftRight className="h-4 w-4 text-brand" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-ink">Mapping Review</div>
            <div className="text-[11px] text-ink-muted">
              {isEbs ? (
                <span className="inline-flex items-center gap-1 text-emerald-700">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="font-mono text-emerald-800">{ebsTable || "Oracle EBS"}</span>
                  <span className="text-emerald-600">· live</span>
                </span>
              ) : (
                <span className="text-ink">{dataset?.name}</span>
              )}
              <span className="mx-1.5">→</span>
              <span className="text-ink">{project.template_name}</span>
              <span className="ml-1.5 font-mono text-ink-subtle">· {targetFields.length} target fields</span>
            </div>
          </div>

          <select
            className="input !h-8 !w-auto !text-xs"
            value={pid ?? ""}
            onChange={(e) => { const v = e.target.value; setPid(v); setParams({ conversion: v }); }}
          >
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>

          <button
            onClick={() => setShowRecs(!showRecs)}
            className={cn("btn-ghost !h-8", showRecs && "bg-brand-subtle text-brand-dark")}
            title="Toggle recommendations panel"
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span className="ml-1 text-xs">Recommendations</span>
          </button>

          <Button onClick={suggest} loading={running} variant="primary" className="!h-8">
            <Sparkles className="h-3.5 w-3.5" />
            {mappings.length ? "Re-run AI" : "Run AI Mapping"}
          </Button>
        </div>

        {/* Zero-fields warning — template parsed with no columns */}
        {targetFields.length === 0 && (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-xs text-warning-dark">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">
              This FBDI template has <strong>0 target fields</strong> — the Excel file may not have parsed correctly when uploaded.
              Click <strong>Seed Standard Fields</strong> to inject Oracle Fusion standard columns, then re-run mapping.
            </span>
            <button
              onClick={seedFields}
              disabled={seeding}
              className="flex items-center gap-1 rounded border border-warning/50 bg-white px-2 py-1 font-medium hover:bg-warning-subtle disabled:opacity-50"
            >
              {seeding ? <Spinner className="h-3 w-3" /> : <RefreshCw className="h-3 w-3" />}
              Seed Standard Fields
            </button>
          </div>
        )}

        {/* Stats + filters */}
        <div className="mt-3 flex items-center gap-3">
          <Stat label="Target fields"  value={stats.total} />
          <Stat label="Auto-mapped"    value={stats.mapped}    tone="info" />
          <Stat label="Approved"       value={stats.approved}  tone="success" />
          <Stat label="Required gaps"  value={stats.reqMissing} tone="danger" />
          <Stat label="Learned"        value={stats.learned}   tone="brand" />
          <Stat label="From KB"        value={stats.kb}        tone="brand" />

          <div className="flex-1" />

          <div className="flex items-center rounded-md border border-line bg-white p-0.5">
            {([
              { v: "all",      label: "All" },
              { v: "required", label: "Required" },
              { v: "review",   label: "Needs review" },
              { v: "approved", label: "Approved" },
              { v: "unmapped", label: "Required gaps" },
              { v: "kb",       label: "From KB" },
            ] as const).map((f) => (
              <button
                key={f.v}
                onClick={() => setFilter(f.v)}
                className={cn("rounded px-2 py-1 text-[11px] font-medium",
                  filter === f.v ? "bg-brand text-white" : "text-ink-muted hover:text-ink")}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle" />
            <input className="input !h-8 !pl-8 !w-56 !text-xs" placeholder="Search field…"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
      </header>

      {inherited.length > 0 && (
        <div className="border-b border-line bg-gradient-to-r from-brand-subtle/40 to-white px-5 py-2.5 text-[12px]">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-brand text-[10px] font-bold text-white">↶</span>
            <div>
              <div className="font-semibold text-ink">
                {inherited.length} reference standard{inherited.length === 1 ? "" : "s"} inherited from upstream masters
              </div>
              <div className="mt-0.5 leading-snug text-ink-muted">
                The following column{inherited.length === 1 ? " is" : "s are"} auto-prepending master-taught rules at output time —
                no need to re-author. Override here to opt out per column.
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {inherited.map((s, i) => (
                  <span
                    key={`${s.target_field}-${s.master_object}-${s.rule_type}-${i}`}
                    className="inline-flex items-center gap-1 rounded-md border border-brand/30 bg-white px-2 py-0.5 font-mono text-[10.5px] text-brand-dark"
                    title={s.captured_from}
                  >
                    {s.target_field} · {s.rule_type}
                    <span className="ml-1 text-[9px] uppercase text-ink-muted">from {s.master_object}</span>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* EBS live mode but no columns came back — connection unreachable or
          the conversion has no table hint. Keep the page usable. */}
      {isEbs && sourceColumns.length === 0 && (
        <div className="border-b border-line bg-warning-subtle px-5 py-2.5 text-[12px] text-warning-dark">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>
              No live columns returned from Oracle EBS
              {ebsTable ? <> for <span className="font-mono">{ebsTable}</span></> : " (no table hint set)"}.
              {ebsDebug?.stage === "no_connection" && " No Oracle EBS connection is configured — add one under Source Connections."}
              {ebsDebug?.stage === "error" && <> Connection error: <span className="font-mono">{String(ebsDebug.error)}</span>.</>}
              {ebsDebug?.stage === "no_columns" && <> Connected as <span className="font-mono">{String(ebsDebug.username)}</span> but the table was not found in ALL_TAB_COLUMNS.</>}
              {!ebsDebug?.stage && " Confirm the EBS connection is healthy and the table hint is set, then re-run AI Mapping."}
            </span>
          </div>
          {ebsDebug && (
            <div className="mt-1 font-mono text-[10.5px] text-warning-dark/70">
              diag: {JSON.stringify(ebsDebug)}
            </div>
          )}
        </div>
      )}

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Mapping canvas */}
        <MappingCanvas
          sourceColumns={sourceColumns}
          targetFields={targetFields}
          mappings={mappings}
          visibleTargetIds={visibleTargetIds}
          selectedMappingId={selectedMappingId}
          setSelectedMappingId={setSelectedMappingId}
          hoveredSource={hoveredSource}
          setHoveredSource={setHoveredSource}
          hoveredTarget={hoveredTarget}
          setHoveredTarget={setHoveredTarget}
          ruleTargetIds={ruleTargetIds}
          onMapDrop={mapDrop}
          onUnmap={unmap}
          loading={running}
        />

        {/* Selected mapping inspector */}
        {selectedMapping && (
          <MappingInspector
            mapping={selectedMapping}
            sourceColumns={sourceColumns}
            onClose={() => setSelectedMappingId(null)}
            onApprove={(m) => approve(m)}
            onReject={(m) => reject(m)}
            onOverride={(m, newSrc) => override(m, newSrc)}
            onAddCustomRule={(m) => { setRuleAuthorMapping(m); setRuleAuthorOpen(true); }}
          />
        )}

        {/* Recommendations panel */}
        {showRecs && (
          <RecommendationsPanel
            recommendations={recommendations.filter(r => !dismissedRecIds.has(r.id))}
            appliedIds={appliedRecIds}
            learnedIds={learnedRecIds}
            onApply={applyRecommendation}
            onDismiss={(rec) => {
              setAppliedRecIds((prev) => new Set([...prev, rec.id]));
              setTimeout(() => setDismissedRecIds((prev) => new Set([...prev, rec.id])), 1500);
            }}
            className="w-[340px]"
          />
        )}
      </div>

      {toast && (
        <div className="pointer-events-none fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}

      {pid && sourceColumns.length > 0 && (
        <RuleAuthorModal
          open={ruleAuthorOpen}
          onClose={() => setRuleAuthorOpen(false)}
          conversionId={pid}
          fields={targetFields}
          sourceColumns={sourceColumns}
          defaultTargetFieldId={ruleAuthorMapping?.target_field_id ?? null}
          defaultSourceColumn={ruleAuthorMapping?.source_column ?? null}
          onSaved={() => { setRuleAuthorOpen(false); flash("Rule saved & added to library"); }}
        />
      )}
    </div>
  );
};

// ─────── Top KPI pill ───────

const Stat: React.FC<{ label: string; value: number; tone?: "info" | "success" | "danger" | "brand" }> = ({ label, value, tone }) => {
  const text = tone === "info" ? "text-info" :
               tone === "success" ? "text-success" :
               tone === "danger" ? "text-danger" :
               tone === "brand" ? "text-brand-dark" :
               "text-ink";
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={cn("text-base font-semibold tabular-nums", text)}>{value}</span>
      <span className="text-[10.5px] uppercase tracking-wider text-ink-muted">{label}</span>
    </div>
  );
};

// ─────── The visual mapping canvas ───────

interface CanvasProps {
  sourceColumns: DatasetDetail["columns"];
  targetFields: FBDIField[];
  mappings: MappingSuggestion[];
  visibleTargetIds: Set<number>;
  selectedMappingId: number | null;
  setSelectedMappingId: (id: number | null) => void;
  hoveredSource: string | null;
  setHoveredSource: (s: string | null) => void;
  hoveredTarget: number | null;
  setHoveredTarget: (t: number | null) => void;
  ruleTargetIds?: Set<number>;
  onMapDrop?: (targetFieldId: number, sourceColumn: string) => void;
  onUnmap?: (m: MappingSuggestion) => void;
  loading?: boolean;
}

const MappingCanvas: React.FC<CanvasProps> = ({
  sourceColumns, targetFields, mappings, visibleTargetIds,
  selectedMappingId, setSelectedMappingId,
  hoveredSource, setHoveredSource, hoveredTarget, setHoveredTarget,
  ruleTargetIds, onMapDrop, onUnmap, loading,
}) => {
  // Which source column is being dragged, and which target is hovered during a
  // drag — drives the drop-zone highlight for the drag-to-map gesture.
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  // Refs to source/target cards keyed by name/id so we can read their DOM positions
  const sourceRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const targetRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const canvasRef = useRef<HTMLDivElement>(null);

  // Positions to draw lines (re-measured after layout changes)
  const [lines, setLines] = useState<{ id: number; x1: number; y1: number; x2: number; y2: number; mapping: MappingSuggestion }[]>([]);

  const recalc = () => {
    if (!canvasRef.current) return;
    const canvasRect = canvasRef.current.getBoundingClientRect();
    const next: typeof lines = [];
    for (const m of mappings) {
      if (!m.source_column) continue;
      if (!visibleTargetIds.has(m.target_field_id)) continue;
      const src = sourceRefs.current.get(m.source_column);
      const tgt = targetRefs.current.get(m.target_field_id);
      if (!src || !tgt) continue;
      const sr = src.getBoundingClientRect();
      const tr = tgt.getBoundingClientRect();
      next.push({
        id: m.id,
        x1: sr.right - canvasRect.left,
        y1: sr.top + sr.height / 2 - canvasRect.top,
        x2: tr.left - canvasRect.left,
        y2: tr.top + tr.height / 2 - canvasRect.top,
        mapping: m,
      });
    }
    setLines(next);
  };

  useLayoutEffect(() => { recalc(); }, [mappings, visibleTargetIds, sourceColumns, targetFields]);

  // Recalc on scroll/resize — both the source and target lists scroll independently
  const onScroll = () => recalc();

  useEffect(() => {
    const obs = new ResizeObserver(() => recalc());
    if (canvasRef.current) obs.observe(canvasRef.current);
    window.addEventListener("resize", recalc);
    return () => { obs.disconnect(); window.removeEventListener("resize", recalc); };
  }, []);

  // Sort source columns: mapped first
  const sortedSources = useMemo(() => {
    const used = new Set(mappings.filter((m) => m.source_column).map((m) => m.source_column));
    return [...sourceColumns].sort((a, b) => {
      const ua = used.has(a.column_name) ? 0 : 1;
      const ub = used.has(b.column_name) ? 0 : 1;
      if (ua !== ub) return ua - ub;
      return a.position - b.position;
    });
  }, [sourceColumns, mappings]);

  // Sort target fields: required first, then by sequence
  const sortedTargets = useMemo(() => [...targetFields].sort((a, b) => {
    if (a.required !== b.required) return a.required ? -1 : 1;
    return a.sequence - b.sequence;
  }), [targetFields]);

  return (
    <div ref={canvasRef} className="relative flex flex-1 overflow-hidden bg-canvas">
      {/* SVG overlay drawing curves between cards */}
      <svg className="pointer-events-none absolute inset-0 z-10 h-full w-full">
        <defs>
          <marker id="arrow-success" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#10B981" />
          </marker>
          <marker id="arrow-warning" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#F59E0B" />
          </marker>
          <marker id="arrow-danger" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#EF4444" />
          </marker>
          <marker id="arrow-brand" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#6366F1" />
          </marker>
        </defs>
        {lines.map((l) => {
          const m = l.mapping;
          const isApproved = m.status === "approved" || m.status === "overridden";
          const isRejected = m.status === "rejected";
          const tone = isApproved ? "brand" :
                       isRejected ? "danger" :
                       confidenceTone(m.confidence);
          const stroke = { success: "#10B981", warning: "#F59E0B", danger: "#EF4444", brand: "#6366F1" }[tone];
          const dash = isApproved ? undefined :
                       isRejected ? "6 4" : undefined;
          const isSel = selectedMappingId === m.id;
          const isHovered = hoveredSource === m.source_column ||
                           hoveredTarget === m.target_field_id;
          // Bezier control points — clean curve from right of source to left of target
          const dx = (l.x2 - l.x1) * 0.5;
          const path = `M ${l.x1} ${l.y1} C ${l.x1 + dx} ${l.y1}, ${l.x2 - dx} ${l.y2}, ${l.x2} ${l.y2}`;
          return (
            <g key={l.id} className="pointer-events-auto">
              {/* invisible thicker hit area */}
              <path d={path} stroke="transparent" strokeWidth={14} fill="none"
                onClick={() => setSelectedMappingId(m.id)}
                style={{ cursor: "pointer" }}
              />
              <path
                d={path}
                stroke={stroke}
                strokeWidth={isSel || isHovered ? 2.5 : 1.5}
                strokeDasharray={dash}
                fill="none"
                opacity={isSel || isHovered ? 1 : 0.55}
                markerEnd={`url(#arrow-${tone})`}
              />
            </g>
          );
        })}
      </svg>

      {/* Source columns */}
      <div className="flex w-[320px] flex-col border-r border-line bg-white">
        <div className="flex items-center justify-between border-b border-line bg-canvas px-3 py-2">
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Source · {sortedSources.length}
            <span className="ml-1.5 normal-case font-normal text-ink-subtle">· drag onto a target to map</span>
          </div>
          {loading && <Spinner />}
        </div>
        <div className="flex-1 overflow-y-auto p-2" onScroll={onScroll}>
          {sortedSources.map((c) => {
            const mapping = mappings.find((m) => m.source_column === c.column_name);
            const isMapped = !!mapping;
            const tone = mapping ?
              (mapping.status === "approved" ? "success" : confidenceTone(mapping.confidence)) :
              "neutral";
            return (
              <div
                key={c.id}
                ref={(el) => { if (el) sourceRefs.current.set(c.column_name, el); }}
                draggable
                onDragStart={(e) => {
                  setDragSource(c.column_name);
                  e.dataTransfer.setData("text/plain", c.column_name);
                  e.dataTransfer.effectAllowed = "link";
                }}
                onDragEnd={() => { setDragSource(null); setDropTargetId(null); }}
                onClick={() => mapping && setSelectedMappingId(mapping.id)}
                onMouseEnter={() => setHoveredSource(c.column_name)}
                onMouseLeave={() => setHoveredSource(null)}
                title="Drag onto a target field to map it"
                className={cn(
                  "mb-1 cursor-grab rounded-md border bg-white px-2.5 py-2 transition active:cursor-grabbing",
                  dragSource === c.column_name ? "border-brand ring-2 ring-brand/30 opacity-60" :
                  hoveredSource === c.column_name ? "border-brand bg-brand-subtle/40 shadow-soft" :
                  isMapped ? "border-line" : "border-line/60 opacity-80",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <div className="truncate text-[12px] font-semibold text-ink">{c.column_name}</div>
                      {c.contains_pii ? (
                        <span
                          className="inline-flex items-center gap-0.5 rounded-full bg-danger/10 px-1 py-0.5 text-[8.5px] font-semibold text-danger"
                          title={`Sensitive · ${c.pii_category || "PII"} — must be pseudonymised before load`}
                        >
                          <PiiLockGlyph /> {c.pii_category || "PII"}
                        </span>
                      ) : null}
                    </div>
                    <div className="font-mono text-[10px] text-ink-muted">
                      {c.inferred_type}
                      {c.distinct_count > 0 && ` · ${c.distinct_count} distinct`}
                      {c.null_percent > 0 && (
                        <span className="text-warning"> · {c.null_percent}% null</span>
                      )}
                    </div>
                  </div>
                  {isMapped && (
                    <span className={cn(
                      "inline-block h-2 w-2 rounded-full shrink-0",
                      tone === "success" ? "bg-success" :
                      tone === "warning" ? "bg-warning" : "bg-danger"
                    )} />
                  )}
                </div>
                {(c.sample_values || []).length > 0 && (
                  <div className="mt-1 truncate font-mono text-[10px] text-ink-subtle">
                    {(c.sample_values || []).slice(0, 3).map((v) => String(v)).join(" · ")}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Spacer where lines render — lines live in the absolute SVG above */}
      <div className="flex-1" />

      {/* Target FBDI fields */}
      <div className="flex w-[360px] flex-col border-l border-line bg-white">
        <div className="border-b border-line bg-canvas px-3 py-2 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          Target FBDI · {sortedTargets.length}
        </div>
        <div className="flex-1 overflow-y-auto p-2" onScroll={onScroll}>
          {sortedTargets.map((f) => {
            const mapping = mappings.find((m) => m.target_field_id === f.id);
            const visible = visibleTargetIds.has(f.id);
            return (
              <div
                key={f.id}
                ref={(el) => { if (el) targetRefs.current.set(f.id, el); }}
                onClick={() => mapping && setSelectedMappingId(mapping.id)}
                onMouseEnter={() => setHoveredTarget(f.id)}
                onMouseLeave={() => setHoveredTarget(null)}
                onDragOver={(e) => {
                  if (!onMapDrop) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "link";
                  if (dropTargetId !== f.id) setDropTargetId(f.id);
                }}
                onDragLeave={() => setDropTargetId((cur) => (cur === f.id ? null : cur))}
                onDrop={(e) => {
                  e.preventDefault();
                  const col = e.dataTransfer.getData("text/plain");
                  setDropTargetId(null);
                  setDragSource(null);
                  if (col && onMapDrop) onMapDrop(f.id, col);
                }}
                className={cn(
                  "mb-1 cursor-pointer rounded-md border bg-white px-2.5 py-2 transition",
                  !visible && "opacity-30",
                  dropTargetId === f.id ? "border-emerald-500 ring-2 ring-emerald-300 bg-emerald-50" :
                  hoveredTarget === f.id ? "border-brand bg-brand-subtle/40 shadow-soft" :
                  mapping?.status === "approved" ? "border-success/50 bg-success-subtle/30" :
                  mapping?.source_column ? "border-line" : "border-dashed border-line",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <div className="truncate text-[12px] font-semibold text-ink">{f.field_name}</div>
                      {f.required && (
                        <span className="rounded bg-danger-subtle px-1 py-0.5 font-mono text-[9px] font-bold text-danger">REQ</span>
                      )}
                      {mapping?.kb_source && mapping.status === "suggested" && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-brand-subtle px-1 py-0.5 font-mono text-[9px] font-bold text-brand-dark"
                          title={`Pre-filled from ${KB_SOURCE_DISPLAY[mapping.kb_source] || mapping.kb_source} Knowledge Bank · ${(mapping.kb_times_reused ?? 0)} prior reuse${(mapping.kb_times_reused ?? 0) === 1 ? "" : "s"}`}
                        >
                          🧠 KB
                        </span>
                      )}
                      {ruleTargetIds?.has(f.id) && (
                        <span
                          className="inline-flex items-center gap-0.5 rounded bg-violet-100 px-1 py-0.5 font-mono text-[9px] font-bold text-violet-700"
                          title="A transformation rule is attached to this field — it applies during Generate Output"
                        >
                          ƒ rule
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-[10px] text-ink-muted">
                      {f.data_type || "Character"}
                      {f.max_length && ` (${f.max_length})`}
                    </div>
                  </div>
                  {mapping && (
                    <Pill tone={statusTone(mapping.status)} className="!text-[9px]">
                      {mapping.status === "suggested" ? `${Math.round(mapping.confidence * 100)}%` :
                       mapping.status}
                    </Pill>
                  )}
                  {mapping?.source_column && onUnmap && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onUnmap(mapping); }}
                      title={`Clear mapping (${mapping.source_column})`}
                      className="shrink-0 rounded p-0.5 text-ink-subtle hover:bg-danger-subtle hover:text-danger"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
                {f.required && !mapping?.source_column && (
                  <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-danger">
                    <AlertTriangle className="h-2.5 w-2.5" /> Required field unmapped
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// ─────── Side inspector for a selected mapping ───────

const MappingInspector: React.FC<{
  mapping: MappingSuggestion;
  sourceColumns: DatasetDetail["columns"];
  onClose: () => void;
  onApprove: (m: MappingSuggestion) => void;
  onReject: (m: MappingSuggestion) => void;
  onOverride: (m: MappingSuggestion, src: string) => void;
  onAddCustomRule: (m: MappingSuggestion) => void;
}> = ({ mapping, sourceColumns, onClose, onApprove, onReject, onOverride, onAddCustomRule }) => {
  const [editingOverride, setEditingOverride] = useState(false);
  const [override, setOverride] = useState(mapping.source_column || "");

  useEffect(() => { setOverride(mapping.source_column || ""); setEditingOverride(false); }, [mapping.id]);

  const tone = confidenceTone(mapping.confidence);
  const cb = { success: "bg-success", warning: "bg-warning", danger: "bg-danger" }[tone];
  const conf = Math.round(mapping.confidence * 100);

  return (
    <aside className="flex w-[400px] shrink-0 flex-col border-l border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Mapping</div>
          <div className="truncate text-sm font-semibold text-ink">{mapping.target_field_name}</div>
        </div>
        <button onClick={onClose} className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {/* Source / Target */}
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Source</div>
            <div className="mt-1 rounded-md border border-line bg-canvas px-2.5 py-2">
              <div className="flex items-center gap-1.5">
                <div className="font-mono text-[12px] text-ink">{mapping.source_column || "— (none)"}</div>
                {(() => {
                  const col = sourceColumns.find((c) => c.column_name === mapping.source_column);
                  return col?.contains_pii ? (
                    <span
                      className="inline-flex items-center gap-0.5 rounded-full bg-danger/10 px-1.5 py-0.5 text-[9.5px] font-semibold text-danger"
                      title={`Sensitive · ${col.pii_category || "PII"} — must be pseudonymised before load`}
                    >
                      <Lock className="h-2.5 w-2.5" /> {col.pii_category || "PII"}
                    </span>
                  ) : null;
                })()}
              </div>
            </div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Target</div>
            <div className="mt-1 rounded-md border border-line bg-canvas px-2.5 py-2">
              <div className="font-mono text-[12px] text-ink">{mapping.target_field_name}</div>
              <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-ink-muted">
                {mapping.target_data_type}{mapping.target_max_length ? ` (${mapping.target_max_length})` : ""}
                {mapping.target_required && <span className="text-danger">required</span>}
              </div>
            </div>
          </div>
        </div>

        {/* Knowledge Bank provenance — shown only when the row came from a
            prior project on the same source ERP. */}
        {mapping.kb_source && (
          <div className="mt-4 rounded-md border border-brand/30 bg-brand-subtle/30 px-3 py-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-1 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
                🧠 From {KB_SOURCE_DISPLAY[mapping.kb_source] || mapping.kb_source} Knowledge Bank
              </span>
              <span className="font-mono text-[10.5px] text-brand-dark">
                {(mapping.kb_times_reused ?? 0)} prior reuse{(mapping.kb_times_reused ?? 0) === 1 ? "" : "s"}
              </span>
            </div>
            <div className="mt-1 leading-snug text-ink">
              This source → target pair was approved on a prior {KB_SOURCE_DISPLAY[mapping.kb_source] || mapping.kb_source} engagement. Confirm it
              fits this customer before approving — the Knowledge Bank pre-fills, it doesn't auto-approve.
            </div>
          </div>
        )}

        {/* Confidence */}
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              {mapping.kb_source ? "Knowledge Bank confidence" : "AI confidence"}
            </span>
            <span className="font-mono text-xs tabular-nums">{conf}%</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-line">
            <div className={cn("h-full rounded-full", cb)} style={{ width: `${conf}%` }} />
          </div>
        </div>

        {/* Reason */}
        {mapping.reason && (
          <div className="mt-4 rounded-md bg-info-subtle/60 px-3 py-2 text-xs text-ink">
            <div className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-info">
              <Sparkles className="h-3 w-3" /> AI explanation
            </div>
            <div className="mt-1 leading-snug">{mapping.reason}</div>
          </div>
        )}

        {/* Suggested transformation */}
        {mapping.suggested_transformation && (
          <div className="mt-4 rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-[10.5px] font-semibold uppercase tracking-wider text-warning">Suggested transformation</span>
              <Pill tone="warning">{mapping.suggested_transformation.rule_type}</Pill>
            </div>
            {mapping.suggested_transformation.description && (
              <div className="mt-1 text-ink-muted">{mapping.suggested_transformation.description}</div>
            )}
          </div>
        )}

        {/* Sample values */}
        {(mapping.sample_source_values || []).length > 0 && (
          <div className="mt-4">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Sample source values</div>
            <div className="mt-1 space-y-0.5">
              {(mapping.sample_source_values || []).slice(0, 5).map((v, i) => (
                <div key={i} className="rounded bg-canvas px-2 py-1 font-mono text-[11px] text-ink">{String(v)}</div>
              ))}
            </div>
          </div>
        )}

        {/* Override editor */}
        <div className="mt-5 space-y-2">
          <button
            onClick={() => onAddCustomRule(mapping)}
            className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
          >
            <Sparkles className="h-3 w-3" /> Add custom transformation rule
          </button>
          {!editingOverride ? (
            <button
              onClick={() => setEditingOverride(true)}
              className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
            >
              <Edit2 className="h-3 w-3" /> Override source column
            </button>
          ) : (
            <div className="rounded-md border border-line bg-canvas p-2.5">
              <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Override source</div>
              <div className="mt-1 flex items-center gap-1.5">
                <select className="input !h-8 !text-xs" value={override}
                  onChange={(e) => setOverride(e.target.value)}>
                  <option value="">— none —</option>
                  {sourceColumns.map((c) => <option key={c.id} value={c.column_name}>{c.column_name}</option>)}
                </select>
                <Button onClick={() => { onOverride(mapping, override); setEditingOverride(false); }} className="!h-8">
                  Save
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* P6 — dual-cert banner */}
      {!!mapping.requires_dual_approval && (
        <div className="border-t border-line bg-warning-subtle/60 px-4 py-2 text-[11.5px]">
          <div className="flex items-center gap-1.5 font-semibold text-warning">
            <Lock className="h-3 w-3" /> Dual-cert required
          </div>
          <div className="mt-0.5 leading-snug text-ink-muted">
            {mapping.status === "approved" ? (
              <>
                Both sign-offs captured · 1st by{" "}
                <span className="font-mono text-ink">{mapping.approved_by || "—"}</span> · 2nd by{" "}
                <span className="font-mono text-ink">{mapping.second_approver_email || "—"}</span>
              </>
            ) : mapping.approved_by ? (
              <>
                1st sign-off captured from{" "}
                <span className="font-mono text-ink">{mapping.approved_by}</span>. A{" "}
                <strong>different</strong> user must approve as 2nd sign-off
                before this mapping flips to approved.
              </>
            ) : (
              <>
                This field is on the dual-cert list (PII / SOX / customer banking).
                Two distinct approvers required.
              </>
            )}
          </div>
        </div>
      )}

      {/* Action footer */}
      <div className="border-t border-line bg-canvas px-4 py-3">
        {mapping.status === "approved" ? (
          <div className="text-center text-xs text-success">
            <Check className="mx-auto h-4 w-4" />
            {mapping.approved_by === "learning-engine" ? (
              <span className="inline-flex items-center gap-1 text-brand-dark">
                <GraduationCap className="h-3 w-3" />
                Auto-applied from learning library
              </span>
            ) : (
              <>
                Approved by {mapping.approved_by || "—"}
                {mapping.second_approver_email && (
                  <> · 2nd by {mapping.second_approver_email}</>
                )}
                <span className="ml-1 inline-flex items-center gap-1 text-brand-dark">
                  <GraduationCap className="h-3 w-3" /> Learned
                </span>
              </>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <Button variant="secondary" onClick={() => onReject(mapping)} className="!h-8">
              <X className="h-3.5 w-3.5" /> Reject
            </Button>
            <Button variant="primary" onClick={() => onApprove(mapping)} className="!h-8">
              <Check className="h-3.5 w-3.5" /> Approve
            </Button>
          </div>
        )}
      </div>
    </aside>
  );
};
