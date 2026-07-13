import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  Sparkles, Check, X, RefreshCw, Search, Filter as FilterIcon,
  GraduationCap, Edit2, ArrowLeft, ArrowLeftRight, AlertTriangle, ChevronDown, Lock, Download,
  GitBranch, Table2, ArrowRight,
} from "lucide-react";

// P3 — tiny lock glyph for source-column PII badges in the canvas list.
const PiiLockGlyph: React.FC = () => <Lock className="h-2 w-2" />;
import { ConversionsApi, DatasetsApi, FbdiApi, InheritedStandardsApi, LearningApi, MappingApi, OutputApi, ProjectsApi } from "@/api";
import LearnFromExamplePanel from "@/components/learn/LearnFromExamplePanel";
import type { InheritedStandard, ReferenceStandard } from "@/api";
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
  MappingCandidate,
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

// Map a target object to its fan-out catalog key so we can show the FBDI
// load-sequence at the top of the mapping screen.
// Output-time control defaults (mirror of backend output_service._CONTROL_DEFAULTS
// / _SEQ_FIELDS). These fields are filled with a fixed value (or a running key)
// at Generate Output even when no source column maps to them — they're
// standardization constants, not data pulled from the extract. Used to show a
// "defaulted → value" state instead of a misleading "required gap".
const CONTROL_DEFAULTS: Record<string, string> = {
  "import action": "CREATE", "batch id": "900001",
  "tax organization type": "Corporation", "organization type": "Corporation",
  "supplier type": "Supplier", "business relationship": "PROSPECTIVE",
  "federal reportable": "N", "delivery channel": "EMAIL",
  "address name": "PRIMARY", "pay": "Y", "ordering": "Y", "rfq or bidding": "Y",
  "supplier site": "PRIMARY", "administrative contact": "Y", "user account action": "NONE",
};
const SEQ_FIELDS = new Set([
  "suppliernumber", "supplierpartynumber", "partynumber",
  "customeraccountnumber", "customernumber",
]);
function normFieldKey(fieldName?: string | null): string {
  return (fieldName || "").toLowerCase().replace(/\*/g, "").trim();
}
function controlDefaultFor(fieldName?: string | null): string | null {
  if (!fieldName) return null;
  const k = normFieldKey(fieldName);
  if (SEQ_FIELDS.has(k.replace(/\s+/g, ""))) return "auto-number (100000+)";
  return CONTROL_DEFAULTS[k] ?? null;
}

function seqKeyForTarget(target?: string | null): string | null {
  const s = (target || "").toLowerCase();
  if (/supplier|vendor/.test(s)) return "supplier";
  if (/customer|client/.test(s)) return "customer";
  if (/\bitem\b|product|material/.test(s)) return "item";
  if (/journal|\bgl\b/.test(s)) return "gl_journal";
  if (/autoinvoice|receivable/.test(s)) return "ar_invoice";
  if (/payable|\bap\b/.test(s)) return "ap_invoice";
  return null;
}

export const MappingReviewPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const projParam = params.get("conversion");

  const [projects, setProjects] = useState<Conversion[]>([]);  // all conversions
  const [pid, setPid] = useState<string | null>(projParam ?? null);
  // Engagement (project) selector — scopes the conversion dropdown so the same
  // conversion names across 22 engagements are no longer ambiguous.
  const [engagements, setEngagements] = useState<import("@/types").Project[]>([]);
  const [engagementId, setEngagementId] = useState<string | null>(null);

  const [project, setProject] = useState<Conversion | null>(null);
  const [loadingConversion, setLoadingConversion] = useState(false);
  // Initial load of the conversion list + engagements (drives the landing view).
  const [loadingList, setLoadingList] = useState(true);
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
  // Values Generate Output writes for unmapped target fields (control constants,
  // sequence keys, learned + AI-inferred defaults), keyed by normalized field
  // name. Lets the canvas show "defaulted → value" instead of a red required gap.
  const [effectiveDefaults, setEffectiveDefaults] = useState<Record<string, string>>({});
  // Cascade visibility — when an upstream master has taught a rule
  // (e.g. REMOVE_HYPHEN on Item.InventoryItemNumber), the matching FK
  // columns on this conversion inherit that rule at output time. We
  // surface them as a banner + per-row chips so the analyst can see
  // the propagation without having to open Output Preview.
  const [inherited, setInherited] = useState<InheritedStandard[]>([]);
  // Gold reference standard stored in the DB for this conversion's object type
  // (auto-applied; no re-upload needed). Null when none is on file yet.
  const [refStd, setRefStd] = useState<ReferenceStandard | null>(null);

  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<FilterMode>("all");
  // Canvas (drag-to-map graph) vs Table (row-per-field detail: required, how it
  // was mapped, transform, confidence, lower-probability alternatives, notes).
  const [viewMode, setViewMode] = useState<"canvas" | "table">("canvas");
  const [search, setSearch] = useState("");
  // FBDI load-sequence shown at the top of the mapping screen (Req 2).
  const [seqSteps, setSeqSteps] = useState<{ label: string; load_order: number }[]>([]);
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

  // Load all conversions + engagements on mount. We deliberately do NOT
  // auto-select a conversion: with none selected the page shows a project-wise
  // landing (engagement dropdown + that project's conversion cards). A conversion
  // is only opened when the user picks one (or a ?conversion= param is present).
  useEffect(() => {
    Promise.all([
      ConversionsApi.list().catch(() => null),
      ProjectsApi.list().catch(() => []),
    ]).then(([ps, engs]: [any, any]) => {
      setEngagements(engs || []);
      if (!ps) {
        setLoadError("Couldn't load the conversion list — the backend may be busy running live Oracle EBS queries. Retry in a moment.");
        setLoadingList(false);
        return;
      }
      setProjects(ps);
      // Default the engagement selector. An ?engagement= param (set by the
      // "Mapping list" back button) wins so the user lands on the same
      // engagement they were reviewing; otherwise pick the first project that
      // has conversions so the landing list is populated immediately.
      if (!engagementId && !pid) {
        const engParam = params.get("engagement");
        const withConv = (engs || []).find((e: any) =>
          ps.some((c: any) => String(c.project_id) === String(e.id)));
        const def = engParam
          ?? withConv?.id ?? (engs && engs[0]?.id) ?? (ps[0] ? String(ps[0].project_id) : null);
        if (def) setEngagementId(String(def));
      }
      setLoadingList(false);
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
    if (proj.project_id != null) setEngagementId(String(proj.project_id));

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
      setEffectiveDefaults({});
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
      // Reference standard on file for this object (from the learning library).
      const objKey = (proj.target_object || "").trim().toLowerCase();
      if (objKey) {
        LearningApi.referenceStandards()
          .then((rows) => setRefStd(rows.find((r) => (r.business_object || "").trim().toLowerCase() === objKey) || null))
          .catch(() => setRefStd(null));
      } else {
        setRefStd(null);
      }
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

    // Effective defaults (control constants + learned + AI-inferred) for unmapped
    // target fields → drives the "defaulted → value" badges. Fetched separately
    // so the optional AI inference pass never blocks the canvas render.
    ConversionsApi.effectiveDefaults(pid)
      .then((r) => setEffectiveDefaults(r.defaults || {}))
      .catch(() => setEffectiveDefaults({}));
  };
  useEffect(() => { loadAll(); }, [pid]);

  // Load the FBDI file-set sequence for the current object (supplier → 7, …).
  useEffect(() => {
    const key = seqKeyForTarget(project?.target_object || project?.template_name);
    if (!key) { setSeqSteps([]); return; }
    let alive = true;
    ConversionsApi.objectTypes()
      .then((types) => { if (alive) setSeqSteps((types.find((t) => t.key === key)?.steps) || []); })
      .catch(() => { if (alive) setSeqSteps([]); });
    return () => { alive = false; };
  }, [project?.target_object, project?.template_name]);

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

  // Generate the FBDI output for this conversion and download it. Multi-sheet
  // objects come back as a .zip (one CSV per interface sheet); the artifact's
  // real file_name carries the correct extension.
  const [generating, setGenerating] = useState(false);
  const generateAndDownload = async () => {
    if (!pid) return;
    setGenerating(true);
    try {
      const out = await OutputApi.generate(pid, "csv");
      const fallback = `${(project?.target_object || project?.name || "fbdi").replace(/[^\w.-]+/g, "_")}.csv`;
      await OutputApi.download(pid, out.file_name || fallback);
      flash("FBDI output generated and downloaded.");
    } catch (e: any) {
      flash(e?.response?.data?.detail || "Failed to generate output");
    } finally { setGenerating(false); }
  };

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
    const nameById = new Map(targetFields.map((f) => [f.id, f.field_name]));
    // A required field is only a genuine gap when it has neither a source column
    // nor any default (learned default_value, static control constant, sequence
    // key, or an AI-inferred/effective default). Defaulted fields are handled at
    // Generate Output, so they must not inflate the "required gaps" count.
    const reqMissing = scoped.filter((m) => {
      if (!m.target_required || m.source_column || m.status === "approved") return false;
      if (m.default_value) return false;
      const fname = nameById.get(m.target_field_id);
      if (controlDefaultFor(fname)) return false;
      if (effectiveDefaults[normFieldKey(fname)]) return false;
      return true;
    }).length;
    const learned = scoped.filter(
      (m) => m.status === "approved" &&
        (m.approved_by === "learning-engine" || m.comment?.includes("[learned]"))
    ).length;
    const kb = scoped.filter((m) => !!m.kb_source).length;
    return { total, mapped, approved, reqMissing, learned, kb };
  }, [mappings, targetFields, effectiveDefaults]);

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

  // ── Landing: project-wise conversion list (no conversion selected) ──────────
  if (!pid) {
    if (loadingList) return <PageLoader />;
    if (loadError) return (
      <div className="p-6">
        <EmptyState
          icon={<AlertTriangle className="h-5 w-5" />}
          title="Couldn't load mapping review"
          description={loadError}
        />
        <div className="mt-3 flex justify-center">
          <Button variant="primary" onClick={() => window.location.reload()}>
            <RefreshCw className="h-4 w-4" /> Retry
          </Button>
        </div>
      </div>
    );

    const scoped = projects.filter(
      (c) => !engagementId || String(c.project_id) === engagementId
    );
    const tone = (s?: string) => statusTone(s || "draft");

    return (
      <div className="space-y-4 p-6">
        <PageTitle
          title="Mapping Review"
          subtitle="Pick an engagement to review the mapping status of its conversions, then open one to map fields."
        />

        <div className="flex flex-wrap items-center gap-3">
          <ArrowLeftRight className="h-4 w-4 text-brand" />
          <label className="text-xs font-medium text-ink-muted">Engagement</label>
          <select
            className="input !h-9 !w-auto"
            value={engagementId ?? ""}
            onChange={(e) => setEngagementId(e.target.value || null)}
          >
            <option value="" disabled>— select engagement —</option>
            {engagements.map((eng) => (
              <option key={eng.id} value={eng.id}>{eng.name}</option>
            ))}
          </select>
          <span className="text-xs text-ink-subtle">
            {scoped.length} conversion{scoped.length === 1 ? "" : "s"}
          </span>
        </div>

        {!engagementId ? (
          <EmptyState
            icon={<ArrowLeftRight className="h-5 w-5" />}
            title="Select an engagement"
            description="Choose a project from the dropdown to see its conversions and mapping status."
          />
        ) : scoped.length === 0 ? (
          <EmptyState
            icon={<ArrowLeftRight className="h-5 w-5" />}
            title="No conversions in this engagement"
            description="This project has no conversion objects yet."
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {scoped.map((c) => {
              const src = (c as any).ebs_table_hint || (c as any).dataset_name;
              const hasTpl = !!c.template_id;
              return (
                <button
                  key={c.id}
                  onClick={() => { setPid(c.id); setParams({ conversion: String(c.id) }); }}
                  className="group flex flex-col gap-2 rounded-lg border border-line bg-white p-4 text-left transition hover:border-brand hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-ink">{c.name}</div>
                      {c.target_object && (
                        <div className="truncate text-[11px] text-ink-subtle">{c.target_object}</div>
                      )}
                    </div>
                    <Pill tone={tone(c.status)}>{c.status}</Pill>
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                    {src ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                        <span className="font-mono text-emerald-800">{src}</span>
                      </span>
                    ) : (
                      <span className="text-ink-subtle">No source linked</span>
                    )}
                    <span className="mx-0.5">→</span>
                    <span className={cn(hasTpl ? "text-ink" : "text-danger")}>
                      {c.template_name || "No FBDI template"}
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] font-medium text-brand opacity-0 transition group-hover:opacity-100">
                    Open mapping →
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // ── Canvas: a single conversion's field mapping ────────────────────────────
  if (loadingConversion) return <PageLoader />;
  if (loadError) return (
    <div className="p-6">
      <EmptyState
        icon={<AlertTriangle className="h-5 w-5" />}
        title="Couldn't load this conversion"
        description={loadError}
      />
      <div className="mt-3 flex justify-center">
        <Button variant="primary" onClick={() => (pid ? loadAll() : window.location.reload())}>
          <RefreshCw className="h-4 w-4" /> Retry
        </Button>
      </div>
    </div>
  );
  if (!project) return <PageLoader />;

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
          <button
            onClick={() => {
              // Return to this engagement's mapping list (clear the open
              // conversion but keep the engagement context so the right
              // project's cards show). Fixes the broken back flow (Req 9).
              setSelectedMappingId(null);
              setPid(null);
              setProject(null);
              const eid = engagementId || (project ? String(project.project_id) : "");
              setParams(eid ? { engagement: eid } : {});
            }}
            className="btn-ghost !h-8 shrink-0"
            title="Back to this engagement's mapping list"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span className="ml-1 text-xs">Mapping list</span>
          </button>
          {project?.project_id != null && (
            <button
              onClick={() => nav(`/projects/${project.project_id}`)}
              className="btn-ghost !h-8 shrink-0"
              title="Back to the engagement overview"
            >
              <span className="text-xs">Project</span>
            </button>
          )}
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

          {/* Engagement (project) selector — scopes the conversion list below. */}
          <select
            className="input !h-8 !w-auto !text-xs"
            value={engagementId ?? ""}
            onChange={(e) => {
              const eid = e.target.value;
              setEngagementId(eid);
              // Jump to that engagement's first conversion.
              const first = projects.find((c) => String(c.project_id) === eid);
              if (first) { setPid(first.id); setParams({ conversion: String(first.id) }); }
            }}
            title="Engagement"
          >
            <option value="" disabled>— engagement —</option>
            {engagements.map((eng) => <option key={eng.id} value={eng.id}>{eng.name}</option>)}
          </select>
          {/* FBDI-file (target template) selector — Req 6: switch which of the
              engagement's generated FBDI files you're reviewing. */}
          <select
            className="input !h-8 !w-auto !text-xs"
            value={pid ?? ""}
            onChange={(e) => { const v = e.target.value; setPid(v); setParams({ conversion: v }); }}
            title="FBDI file — switch between the templates generated for this engagement"
          >
            {projects
              .filter((c) => !engagementId || String(c.project_id) === engagementId)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {(p as any).template_name || p.target_object || p.name}
                </option>
              ))}
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

          <Button
            onClick={generateAndDownload}
            loading={generating}
            variant="secondary"
            className="!h-8"
            disabled={!mappings.some((m) => m.source_column)}
            title="Generate this object's FBDI file and download it"
          >
            <Download className="h-3.5 w-3.5" />
            <span className="ml-1 text-xs">Generate &amp; download</span>
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

          {/* View toggle — canvas graph vs tabular field-mapping detail */}
          <div className="flex items-center rounded-md border border-line bg-white p-0.5">
            {([
              { v: "canvas", label: "Canvas", Icon: GitBranch },
              { v: "table",  label: "Table",  Icon: Table2 },
            ] as const).map((o) => (
              <button
                key={o.v}
                onClick={() => setViewMode(o.v)}
                title={o.v === "table"
                  ? "Tabular view — source → target with required flag, how it was mapped, transform, confidence, and lower-probability alternatives"
                  : "Canvas view — drag source columns onto target fields"}
                className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium",
                  viewMode === o.v ? "bg-brand text-white" : "text-ink-muted hover:text-ink")}
              >
                <o.Icon className="h-3 w-3" /> {o.label}
              </button>
            ))}
          </div>

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

      {/* FBDI load-sequence map — click a file to review its mappings (Req 2/6) */}
      {seqSteps.length > 1 && (
        <div className="border-b border-line bg-white px-5 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
            Load sequence · {seqSteps.length} FBDI files
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {seqSteps.map((s, i) => {
              const sc = projects.find(
                (c) =>
                  (!engagementId || String(c.project_id) === engagementId) &&
                  ((c.target_object || "").toLowerCase() === s.label.toLowerCase() ||
                    (c.template_name || "").toLowerCase() === s.label.toLowerCase())
              );
              const isCurrent = !!sc && String(sc.id) === String(pid);
              return (
                <React.Fragment key={s.label}>
                  <button
                    disabled={!sc}
                    onClick={() => { if (sc) { setPid(String(sc.id)); setParams({ conversion: String(sc.id) }); } }}
                    title={sc ? "Show this FBDI file's mappings" : "Not generated yet"}
                    className={cn(
                      "rounded border px-2 py-1 text-[11px] transition",
                      isCurrent
                        ? "border-brand bg-brand-subtle font-semibold text-brand-dark"
                        : sc
                        ? "border-line bg-white text-ink hover:border-brand"
                        : "cursor-default border-dashed border-line bg-canvas text-ink-subtle"
                    )}
                  >
                    <span className="font-mono text-ink-subtle">{s.load_order}.</span> {s.label}
                  </button>
                  {i < seqSteps.length - 1 && <span className="text-ink-subtle">→</span>}
                </React.Fragment>
              );
            })}
          </div>
        </div>
      )}

      {pid && (
        <LearnFromExamplePanel conversionId={String(pid)} onApplied={loadAll} />
      )}

      {pid && refStd && (
        <div className="border-b border-line bg-brand-subtle/15 px-5 py-2 text-[12px]">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-brand text-[10px] font-bold text-white">✓</span>
            <div>
              <div className="font-semibold text-ink">
                Gold reference standard on file for {refStd.business_object} — auto-applied
              </div>
              <div className="mt-0.5 leading-snug text-ink-muted">
                Stored from a previously uploaded gold output: {refStd.column_mappings} mapped column{refStd.column_mappings === 1 ? "" : "s"},
                {" "}{refStd.defaults} constant default{refStd.defaults === 1 ? "" : "s"}, {refStd.suppressions} suppressed field{refStd.suppressions === 1 ? "" : "s"}.
                No need to re-upload — uploading a new gold file above overrides it for this object.
              </div>
            </div>
          </div>
        </div>
      )}

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
        {viewMode === "table" ? (
          <MappingTableView
            conversionId={pid ?? ""}
            sourceColumns={sourceColumns}
            targetFields={targetFields}
            mappings={mappings}
            visibleTargetIds={visibleTargetIds}
            effectiveDefaults={effectiveDefaults}
            ruleTargetIds={ruleTargetIds}
            selectedMappingId={selectedMappingId}
            setSelectedMappingId={setSelectedMappingId}
            onOverride={(m, src) => override(m, src)}
            loading={running}
          />
        ) : (
        /* Mapping canvas */
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
          effectiveDefaults={effectiveDefaults}
          onMapDrop={mapDrop}
          onUnmap={unmap}
          loading={running}
        />
        )}

        {/* Selected mapping inspector */}
        {selectedMapping && (
          <MappingInspector
            mapping={selectedMapping}
            sourceColumns={sourceColumns}
            conversionId={pid ?? ""}
            targetObject={project?.target_object}
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

// ─────── Tabular mapping view ───────
// One row per target FBDI field: the source it's drawn from, whether the target
// is mandatory, HOW the value is produced (learned / rule-based / AI / constant
// default / suppressed), any transform, the confidence, and the lower-probability
// alternative source columns the ranker considered (click one to re-map).

/** Derive a human-readable "how was this mapped" chip from the mapping row. */
function mappingMethod(
  m: MappingSuggestion | undefined,
  f: FBDIField,
  effectiveDefaults: Record<string, string>,
  hasRule: boolean,
): { label: string; tone: "brand" | "info" | "success" | "warning" | "neutral"; detail?: string } {
  const dv = m?.default_value || controlDefaultFor(f.field_name) || effectiveDefaults[normFieldKey(f.field_name)];
  if (m?.status === "not_applicable") {
    return { label: "Suppressed", tone: "neutral", detail: "Blank in the gold standard — intentionally left empty" };
  }
  if (!m?.source_column) {
    if (dv) return { label: "Constant default", tone: "info", detail: `Written as “${dv}” on every row` };
    return { label: "Unmapped", tone: "neutral", detail: "No source column and no default" };
  }
  const reason = (m.reason || "").toLowerCase();
  if (m.status === "overridden") return { label: "Manual override", tone: "warning", detail: m.reason || undefined };
  if (reason.includes("learning library") || reason.includes("gold") || reason.includes("learned")) {
    return { label: "Learned (KB)", tone: "brand", detail: m.reason || undefined };
  }
  if (reason.includes("value-set") || reason.includes("example")) {
    return { label: "Learned from gold", tone: "brand", detail: m.reason || undefined };
  }
  if (hasRule || m.suggested_transformation?.rule_type) {
    return { label: "Rule / transform", tone: "info", detail: m.reason || undefined };
  }
  if ((m.confidence ?? 0) >= 0.6) return { label: "Rule-based match", tone: "success", detail: m.reason || undefined };
  return { label: "AI suggested", tone: "warning", detail: m.reason || undefined };
}

const MethodChip: React.FC<{ tone: string; children: React.ReactNode; title?: string }> = ({ tone, children, title }) => (
  <span
    title={title}
    className={cn(
      "inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium",
      tone === "brand" && "bg-brand-subtle text-brand-dark",
      tone === "info" && "bg-info-subtle text-info",
      tone === "success" && "bg-success-subtle text-success",
      tone === "warning" && "bg-warning-subtle text-warning-dark",
      tone === "neutral" && "bg-canvas text-ink-muted",
    )}
  >
    {children}
  </span>
);

const MappingTableView: React.FC<{
  conversionId: string;
  sourceColumns: DatasetDetail["columns"];
  targetFields: FBDIField[];
  mappings: MappingSuggestion[];
  visibleTargetIds: Set<number>;
  effectiveDefaults: Record<string, string>;
  ruleTargetIds: Set<number>;
  selectedMappingId: number | null;
  setSelectedMappingId: (id: number | null) => void;
  onOverride: (m: MappingSuggestion, newSrc: string) => void;
  loading?: boolean;
}> = ({
  conversionId, sourceColumns, targetFields, mappings, visibleTargetIds,
  effectiveDefaults, ruleTargetIds, selectedMappingId, setSelectedMappingId, onOverride, loading,
}) => {
  // Ranked alternatives for every target field (one round-trip), so each row can
  // show the runner-up source columns the matcher scored lower.
  const [altByTarget, setAltByTarget] = useState<Record<string, MappingCandidate[]>>({});
  const [altLoading, setAltLoading] = useState(true);
  useEffect(() => {
    if (!conversionId) return;
    setAltLoading(true);
    MappingApi.candidates(conversionId, { topN: 4 })
      .then((groups) => {
        const byId: Record<string, MappingCandidate[]> = {};
        for (const g of groups) byId[String(g.target_field_id)] = g.candidates || [];
        setAltByTarget(byId);
      })
      .catch(() => setAltByTarget({}))
      .finally(() => setAltLoading(false));
  }, [conversionId]);

  const mapByTarget = useMemo(() => {
    const m: Record<string, MappingSuggestion> = {};
    for (const x of mappings) m[String(x.target_field_id)] = x;
    return m;
  }, [mappings]);

  const srcProfile = useMemo(() => {
    const m: Record<string, DatasetColumnProfile> = {};
    for (const c of sourceColumns) m[c.column_name] = c;
    return m;
  }, [sourceColumns]);

  const rows = targetFields.filter((f) => visibleTargetIds.has(f.id));

  return (
    <div className="flex-1 overflow-auto bg-white">
      {loading && (
        <div className="flex items-center gap-2 border-b border-line bg-canvas px-5 py-2 text-[12px] text-ink-muted">
          <Spinner /> Running mapping…
        </div>
      )}
      <table className="w-full border-collapse text-[12px]">
        <thead className="sticky top-0 z-10 bg-canvas">
          <tr className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
            <th className="border-b border-line px-3 py-2 font-semibold">Source field</th>
            <th className="border-b border-line px-1 py-2" />
            <th className="border-b border-line px-3 py-2 font-semibold">Target FBDI field</th>
            <th className="border-b border-line px-3 py-2 font-semibold">How it's mapped</th>
            <th className="border-b border-line px-3 py-2 font-semibold">Conf.</th>
            <th className="border-b border-line px-3 py-2 font-semibold">Other options (lower probability)</th>
            <th className="border-b border-line px-3 py-2 font-semibold">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => {
            const m = mapByTarget[String(f.id)];
            const hasRule = ruleTargetIds.has(f.id);
            const method = mappingMethod(m, f, effectiveDefaults, hasRule);
            const dv = m?.default_value || controlDefaultFor(f.field_name) || effectiveDefaults[normFieldKey(f.field_name)];
            const prof = m?.source_column ? srcProfile[m.source_column] : undefined;
            const transform = m?.suggested_transformation?.rule_type as string | undefined;
            // Alternatives = ranked candidates minus the one actually chosen.
            const alts = (altByTarget[String(f.id)] || [])
              .filter((c) => c.source_column !== m?.source_column)
              .slice(0, 3);
            const isGap = f.required && !m?.source_column && !dv && m?.status !== "not_applicable";
            const selected = m && selectedMappingId === m.id;
            return (
              <tr
                key={f.id}
                onClick={() => m && setSelectedMappingId(selected ? null : m.id)}
                className={cn(
                  "cursor-pointer align-top border-b border-line/60 hover:bg-canvas/60",
                  selected && "bg-brand-subtle/25",
                  isGap && "bg-danger-subtle/25",
                )}
              >
                {/* Source */}
                <td className="px-3 py-2">
                  {m?.source_column ? (
                    <>
                      <div className="font-mono text-[11.5px] text-ink">{m.source_column}</div>
                      {prof && (
                        <div className="mt-0.5 text-[10px] text-ink-subtle">
                          {prof.inferred_type} · {formatNumber(prof.distinct_count ?? 0)} distinct
                          {prof.null_percent != null && <> · {Number(prof.null_percent).toFixed(1)}% null</>}
                        </div>
                      )}
                    </>
                  ) : (
                    <span className="text-[11px] italic text-ink-subtle">
                      {dv ? "— (constant)" : "— (none)"}
                    </span>
                  )}
                </td>
                <td className="px-1 py-2 text-ink-subtle">
                  <ArrowRight className="h-3 w-3" />
                </td>
                {/* Target */}
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium text-ink">{f.field_name}</span>
                    {f.required && (
                      <span className="rounded bg-danger-subtle px-1 py-0.5 text-[9px] font-bold uppercase text-danger">req</span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[10px] text-ink-subtle">{f.data_type || "Character"}</div>
                </td>
                {/* How mapped */}
                <td className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-1">
                    <MethodChip tone={method.tone} title={method.detail}>{method.label}</MethodChip>
                    {transform && (
                      <MethodChip tone="info" title="Transformation applied to the source value">
                        {transform}
                      </MethodChip>
                    )}
                    {hasRule && !transform && <MethodChip tone="info">custom rule</MethodChip>}
                  </div>
                  {dv && !m?.source_column && (
                    <div className="mt-0.5 font-mono text-[10px] text-info">→ {dv}</div>
                  )}
                  {m && (
                    <div className="mt-0.5">
                      <Pill tone={statusTone(m.status)}>{m.status.replace("_", " ")}</Pill>
                    </div>
                  )}
                </td>
                {/* Confidence */}
                <td className="px-3 py-2">
                  {m?.source_column ? (() => {
                    const t = confidenceTone(m.confidence ?? 0);
                    return (
                      <span className={cn(
                        "font-mono text-[11px] font-medium tabular-nums",
                        t === "success" ? "text-success" : t === "warning" ? "text-warning-dark" : "text-danger",
                      )}>
                        {Math.round((m.confidence ?? 0) * 100)}%
                      </span>
                    );
                  })() : (
                    <span className="text-[11px] text-ink-subtle">—</span>
                  )}
                </td>
                {/* Alternatives */}
                <td className="px-3 py-2">
                  {altLoading ? (
                    <span className="text-[10px] text-ink-subtle">ranking…</span>
                  ) : alts.length === 0 ? (
                    <span className="text-[10px] text-ink-subtle">—</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {alts.map((c) => (
                        <button
                          key={c.source_column}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (m) onOverride(m, c.source_column);
                          }}
                          title={`Re-map to ${c.source_column} — ${(c.reasons || []).join("; ") || "ranked alternative"}${
                            c.sample_values?.length ? `\nSamples: ${c.sample_values.slice(0, 3).join(", ")}` : ""
                          }`}
                          className="inline-flex items-center gap-1 rounded border border-line bg-white px-1.5 py-0.5 font-mono text-[10px] text-ink-muted transition hover:border-brand hover:text-brand-dark"
                        >
                          {c.source_column}
                          <span className="text-[9px] text-ink-subtle">{Math.round((c.confidence ?? 0) * 100)}%</span>
                        </button>
                      ))}
                    </div>
                  )}
                </td>
                {/* Notes */}
                <td className="px-3 py-2 text-[11px] leading-snug text-ink-muted">
                  {isGap
                    ? <span className="font-medium text-danger">Required field with no source and no default.</span>
                    : (m?.reason || method.detail || "—")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="p-8 text-center text-[12px] text-ink-muted">No fields match the current filter.</div>
      )}
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
  effectiveDefaults?: Record<string, string>;
  onMapDrop?: (targetFieldId: number, sourceColumn: string) => void;
  onUnmap?: (m: MappingSuggestion) => void;
  loading?: boolean;
}

const MappingCanvas: React.FC<CanvasProps> = ({
  sourceColumns, targetFields, mappings, visibleTargetIds,
  selectedMappingId, setSelectedMappingId,
  hoveredSource, setHoveredSource, hoveredTarget, setHoveredTarget,
  ruleTargetIds, effectiveDefaults = {}, onMapDrop, onUnmap, loading,
}) => {
  // Which source column is being dragged, and which target is hovered during a
  // drag — drives the drop-zone highlight for the drag-to-map gesture.
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  // Live text filter for the source-column list.
  const [srcQuery, setSrcQuery] = useState("");
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

  useLayoutEffect(() => { recalc(); }, [mappings, visibleTargetIds, sourceColumns, targetFields, srcQuery]);

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

  // Apply the live source filter (name, type, or sample value match).
  const shownSources = useMemo(() => {
    const q = srcQuery.trim().toLowerCase();
    if (!q) return sortedSources;
    return sortedSources.filter((c) =>
      c.column_name.toLowerCase().includes(q) ||
      (c.inferred_type || "").toLowerCase().includes(q) ||
      (c.sample_values || []).some((v) => String(v).toLowerCase().includes(q))
    );
  }, [sortedSources, srcQuery]);

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
            Source · {shownSources.length}{srcQuery && <span className="text-ink-subtle"> / {sortedSources.length}</span>}
            <span className="ml-1.5 normal-case font-normal text-ink-subtle">· drag onto a target to map</span>
          </div>
          {loading && <Spinner />}
        </div>
        <div className="border-b border-line bg-white px-2 py-1.5">
          <input
            value={srcQuery}
            onChange={(e) => setSrcQuery(e.target.value)}
            placeholder="Filter source columns…"
            className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-[12px] text-ink placeholder:text-ink-subtle focus:border-brand focus:outline-none"
          />
        </div>
        <div className="flex-1 overflow-y-auto p-2" onScroll={onScroll}>
          {shownSources.length === 0 && (
            <div className="px-2 py-6 text-center text-[11px] text-ink-subtle">No source columns match “{srcQuery}”.</div>
          )}
          {shownSources.map((c) => {
            const mapping = mappings.find((m) => m.source_column === c.column_name);
            const isMapped = !!mapping;
            const tone = mapping ?
              (mapping.status === "approved" ? "success" : confidenceTone(mapping.confidence)) :
              "neutral";
            return (
              <div
                key={c.id}
                ref={(el) => { if (el) sourceRefs.current.set(c.column_name, el); else sourceRefs.current.delete(c.column_name); }}
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
                {!mapping?.source_column && (() => {
                  const dv = mapping?.default_value || controlDefaultFor(f.field_name) || effectiveDefaults[normFieldKey(f.field_name)];
                  if (dv) {
                    return (
                      <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-emerald-600">
                        <Sparkles className="h-2.5 w-2.5" /> Defaulted → {dv}
                      </div>
                    );
                  }
                  if (f.required) {
                    return (
                      <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-danger">
                        <AlertTriangle className="h-2.5 w-2.5" /> Required field unmapped
                      </div>
                    );
                  }
                  return null;
                })()}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// ─────── AI value-map (LOV crosswalk) recommendations for the selected field ───────
// The VRS requirement: don't map on column names alone — compare the DATA in
// the source column against the destination's list of values and recommend the
// translation pairs (Retire→Inactive, "MRP Planned"→3, Days of Supply→Days of
// cover ...). Unresolved values surface as exceptions needing manual mapping.
const methodLabel: Record<string, string> = {
  exact_code: "exact", exact_meaning: "meaning", synonym: "synonym",
  fuzzy: "fuzzy", learned: "learned", ai: "AI",
};

const ValueMapRecommendationsPanel: React.FC<{
  mapping: MappingSuggestion;
  onApplied?: () => void;
}> = ({ mapping, onApplied }) => {
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [recs, setRecs] = useState<any | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [appliedMsg, setAppliedMsg] = useState<string | null>(null);

  useEffect(() => { setRecs(null); setSelected({}); setAppliedMsg(null); }, [mapping.id]);

  const lov = mapping.target_lov || [];
  // Show for any mapped field: with a stored LOV we resolve deterministically,
  // and without one the AI still normalizes values to standard Fusion codes.
  if (!mapping.source_column) return null;

  const load = async () => {
    setLoading(true); setAppliedMsg(null);
    try {
      const r = await MappingApi.valueMapRecommendations(String(mapping.id));
      setRecs(r);
      // Pre-select every non-identity translation pair
      const sel: Record<string, boolean> = {};
      (r.recommendations || []).forEach((p: any) => { if (!p.already_valid) sel[p.source_value] = true; });
      setSelected(sel);
    } catch (e: any) {
      setRecs({ error: e?.response?.data?.detail || "Failed to load recommendations" });
    } finally { setLoading(false); }
  };

  const apply = async () => {
    if (!recs) return;
    const pairs = (recs.recommendations || [])
      .filter((p: any) => selected[p.source_value])
      .map((p: any) => ({ source_value: p.source_value, target_value: p.target_value }));
    if (!pairs.length) return;
    setApplying(true);
    try {
      const res = await MappingApi.acceptValueMap(String(mapping.id), { pairs });
      setAppliedMsg(`Applied ${res.pairs_applied} value pair${res.pairs_applied !== 1 ? "s" : ""} — saved as a VALUE_MAP rule and learned to the Crosswalk Library.`);
      setRecs(null); setSelected({});
      onApplied?.();
    } catch (e: any) {
      setAppliedMsg(e?.response?.data?.detail || "Failed to apply value map");
    } finally { setApplying(false); }
  };

  const selCount = Object.values(selected).filter(Boolean).length;

  return (
    <div className="mt-4 rounded-md border border-brand/30 bg-brand/5 p-2.5">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
          <Sparkles className="h-3 w-3" /> {lov.length ? "Target list of values" : "AI value normalization"}
        </span>
        {mapping.target_default_if_blank && (
          <span className="text-[10px] text-ink-subtle">blank → {mapping.target_default_if_blank}</span>
        )}
      </div>
      {/* LOV chips — what the destination actually accepts (when defined) */}
      {lov.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {lov.map((e) => (
            <span key={e.code} title={e.meaning || e.code}
              className="rounded border border-line bg-white px-1.5 py-0.5 font-mono text-[10.5px] text-ink">
              {e.code}{e.meaning && e.meaning !== e.code ? ` · ${e.meaning}` : ""}
            </span>
          ))}
        </div>
      )}
      {!lov.length && (
        <div className="mt-1 text-[10px] text-ink-subtle">
          Claude maps this column's values to standard Oracle Fusion codes (e.g. United States → US, Kilogram → KG).
        </div>
      )}

      {!recs && !appliedMsg && (
        <button onClick={load} disabled={loading}
          className="mt-2 inline-flex items-center gap-1 rounded-md border border-brand/40 bg-white px-2 py-1 text-[11px] font-medium text-brand-dark hover:bg-brand/10 disabled:opacity-50">
          {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
          Recommend value mappings
        </button>
      )}

      {recs?.error && (
        <div className="mt-2 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-ink">
          {recs.error}
        </div>
      )}

      {recs && !recs.error && (
        <div className="mt-2">
          <div className="text-[10.5px] text-ink-subtle">
            {recs.distinct_values.length} distinct source value{recs.distinct_values.length !== 1 ? "s" : ""} ·{" "}
            {Math.round((recs.coverage || 0) * 100)}% resolved to a Fusion value
          </div>
          <div className="mt-1.5 max-h-52 space-y-1 overflow-y-auto pr-0.5">
            {(recs.recommendations || []).map((p: any) => (
              <label key={p.source_value}
                className="flex cursor-pointer items-center gap-1.5 rounded border border-line bg-white px-2 py-1 text-[11px]">
                <input type="checkbox" className="h-3 w-3 accent-brand"
                  checked={!!selected[p.source_value]} disabled={p.already_valid}
                  onChange={(e) => setSelected((s) => ({ ...s, [p.source_value]: e.target.checked }))} />
                <span className="font-mono text-danger">{p.source_value}</span>
                <span className="text-ink-subtle">→</span>
                <span className="font-mono text-success">{p.target_value}</span>
                <span className="ml-auto flex items-center gap-1">
                  {p.already_valid ? (
                    <span className="rounded bg-success/10 px-1 py-0.5 text-[9.5px] font-medium text-success">already valid</span>
                  ) : (
                    <span className="rounded bg-canvas px-1 py-0.5 text-[9.5px] text-ink-muted">
                      {methodLabel[p.method] || p.method} · {Math.round(p.confidence * 100)}%
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
          {(recs.unmatched || []).length > 0 && (
            <div className="mt-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-ink">
              <span className="inline-flex items-center gap-1 font-medium">
                <AlertTriangle className="h-3 w-3 text-warning" /> Needs manual mapping:
              </span>{" "}
              {(recs.unmatched || []).map((v: string) => (
                <span key={v} className="mr-1 font-mono">{v}</span>
              ))}
              <div className="mt-0.5 text-[10px] text-ink-subtle">Add these below in Value mappings, or they pass through unchanged.</div>
            </div>
          )}
          <div className="mt-2 flex items-center gap-2">
            <Button onClick={apply} loading={applying} disabled={selCount === 0} className="!h-7 !px-2.5 !text-[11px]">
              Apply {selCount} selected
            </Button>
            <button onClick={() => { setRecs(null); setSelected({}); }} className="text-[11px] text-ink-subtle hover:underline">
              Cancel
            </button>
          </div>
        </div>
      )}

      {appliedMsg && (
        <div className="mt-2 flex items-start gap-1.5 rounded border border-success/40 bg-success/10 px-2 py-1 text-[11px] text-ink">
          <Check className="mt-0.5 h-3 w-3 shrink-0 text-success" /> {appliedMsg}
        </div>
      )}
    </div>
  );
};

// ─────── Inline value-mapping (crosswalk) editor for the selected field ───────
const ValueMappingsPanel: React.FC<{ targetObject?: string | null; targetField?: string | null }> = ({ targetObject, targetField }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [orig, setOrig] = useState("");
  const [res, setRes] = useState("");
  const [saving, setSaving] = useState(false);

  const load = () => {
    LearningApi.list({ kind: "crosswalk" })
      .then((all: any[]) => setRows(all.filter((c) =>
        (!targetObject || (c.target_object || "").toLowerCase() === String(targetObject).toLowerCase()) &&
        (!c.target_field || !targetField || String(c.target_field).toLowerCase() === String(targetField).toLowerCase())
      )))
      .catch(() => setRows([]));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [targetObject, targetField]);

  const add = async () => {
    if (!orig.trim() || !res.trim()) return;
    setSaving(true);
    try {
      await LearningApi.capture({
        kind: "crosswalk",
        category: `Value Mapping — ${targetField || targetObject || "field"}`,
        original_value: orig.trim(),
        resolved_value: res.trim(),
        target_object: targetObject || undefined,
        target_field: targetField || undefined,
      } as any);
      setOrig(""); setRes(""); load();
    } finally { setSaving(false); }
  };

  return (
    <div className="mt-4 rounded-md border border-line bg-canvas/60 p-2.5">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          <ArrowLeftRight className="h-3 w-3" /> Value mappings (crosswalk)
        </span>
        <span className="text-[10px] text-ink-subtle">{rows.length}</span>
      </div>
      <div className="mt-0.5 text-[10.5px] leading-snug text-ink-subtle">
        Translate legacy values into Fusion values for this field. Saved to the Crosswalk Library and reused at output generation.
      </div>

      {rows.length > 0 && (
        <div className="mt-2 space-y-1">
          {rows.map((c) => (
            <div key={c.id} className="flex items-center gap-1.5 rounded border border-line bg-white px-2 py-1 text-[11px]">
              <span className="font-mono text-danger">{c.original_value}</span>
              <span className="text-ink-subtle">→</span>
              <span className="font-mono text-success">{c.resolved_value}</span>
              <button
                onClick={async () => { await LearningApi.delete(c.id); load(); }}
                className="ml-auto rounded p-0.5 text-ink-subtle hover:bg-canvas hover:text-danger"
                title="Remove value mapping"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-1.5">
        <input className="input !h-7 !text-[11px]" placeholder="legacy" value={orig} onChange={(e) => setOrig(e.target.value)} />
        <span className="text-ink-subtle">→</span>
        <input className="input !h-7 !text-[11px]" placeholder="Fusion" value={res} onChange={(e) => setRes(e.target.value)} />
        <Button onClick={add} loading={saving} disabled={!orig.trim() || !res.trim()} className="!h-7 shrink-0 !px-2 !text-[11px]">Add</Button>
      </div>
    </div>
  );
};

// ─────── Alternative source-column candidates for a target field ───────
// Shows the ranked source columns the AI could map this target to, so the
// reviewer can pick a different one for flexibility. The top pick is the
// current auto-mapping; the rest are alternatives.
const AlternativeCandidatesPanel: React.FC<{
  conversionId: string;
  targetFieldId: number | string;
  currentSource: string | null;
  onPick: (sourceColumn: string) => void;
}> = ({ conversionId, targetFieldId, currentSource, onPick }) => {
  const [cands, setCands] = useState<MappingCandidate[] | null>(null);
  const [open, setOpen] = useState(true);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setCands(null);
    MappingApi.candidates(conversionId, { targetFieldId: String(targetFieldId), topN: 6 })
      .then((groups) => {
        if (!alive) return;
        setCands(groups[0]?.candidates ?? []);
      })
      .catch(() => { if (alive) setCands([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [conversionId, targetFieldId]);

  return (
    <div className="mt-4 rounded-md border border-line bg-canvas/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <span className="inline-flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          <ArrowLeftRight className="h-3 w-3" /> Alternative source columns
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 text-ink-muted transition", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <div className="px-3 pb-3">
          {loading ? (
            <div className="flex items-center gap-2 py-2 text-[11px] text-ink-muted">
              <Spinner /> Ranking candidates…
            </div>
          ) : (cands || []).length === 0 ? (
            <div className="py-1 text-[11px] text-ink-subtle">No alternative source columns scored above zero.</div>
          ) : (
            <div className="space-y-1.5">
              {(cands || []).map((c) => {
                const isCurrent = currentSource && c.source_column === currentSource;
                const pct = Math.round((c.confidence || 0) * 100);
                return (
                  <div
                    key={c.source_column}
                    className={cn(
                      "rounded-md border px-2.5 py-1.5",
                      isCurrent ? "border-brand/50 bg-brand-subtle/40" : "border-line bg-white",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <code className="truncate rounded bg-canvas px-1.5 py-0.5 text-[11px] text-ink">{c.source_column}</code>
                          {isCurrent && <Pill tone="brand">current</Pill>}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-ink-subtle">
                          <span className="font-mono tabular-nums">{pct}%</span>
                          {c.inferred_type && <span>· {c.inferred_type}</span>}
                          <span>· {Math.round(100 - (c.null_percent || 0))}% filled</span>
                        </div>
                      </div>
                      {!isCurrent && (
                        <button
                          onClick={() => onPick(c.source_column)}
                          className="shrink-0 rounded border border-brand/40 px-2 py-0.5 text-[11px] font-medium text-brand-dark hover:bg-brand-subtle"
                        >
                          Use
                        </button>
                      )}
                    </div>
                    {c.reasons?.length > 0 && (
                      <div className="mt-1 text-[10px] leading-snug text-ink-muted">{c.reasons.join(" · ")}</div>
                    )}
                    {c.sample_values?.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.sample_values.slice(0, 3).map((v, i) => (
                          <span key={i} className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[10px] text-ink-muted">{String(v)}</span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ─────── Side inspector for a selected mapping ───────

const MappingInspector: React.FC<{
  mapping: MappingSuggestion;
  sourceColumns: DatasetDetail["columns"];
  conversionId: string;
  onClose: () => void;
  onApprove: (m: MappingSuggestion) => void;
  onReject: (m: MappingSuggestion) => void;
  onOverride: (m: MappingSuggestion, src: string) => void;
  onAddCustomRule: (m: MappingSuggestion) => void;
  targetObject?: string | null;
}> = ({ mapping, sourceColumns, conversionId, onClose, onApprove, onReject, onOverride, onAddCustomRule, targetObject }) => {
  const [editingOverride, setEditingOverride] = useState(false);
  const [override, setOverride] = useState(mapping.source_column || "");
  const [vmRefresh, setVmRefresh] = useState(0);

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

        {/* AI value-map recommendations against the target LOV */}
        <ValueMapRecommendationsPanel mapping={mapping} onApplied={() => setVmRefresh((n) => n + 1)} />

        {/* Value mappings (crosswalk) for this field */}
        <ValueMappingsPanel key={vmRefresh} targetObject={targetObject} targetField={mapping.target_field_name} />

        {/* Alternative source-column candidates (for flexibility) */}
        <AlternativeCandidatesPanel
          conversionId={conversionId}
          targetFieldId={mapping.target_field_id}
          currentSource={mapping.source_column}
          onPick={(src) => onOverride(mapping, src)}
        />

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
