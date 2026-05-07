import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Sparkles, Check, X, RefreshCw, Search, Filter as FilterIcon,
  GraduationCap, Edit2, ArrowLeftRight, AlertTriangle, ChevronDown,
} from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, MappingApi } from "@/api";
import { RuleAuthorModal } from "@/components/transforms/RuleAuthorModal";
import {
  Button, Card, CardBody, EmptyState, PageLoader, PageTitle, Pill, Spinner,
} from "@/components/ui/Primitives";
import { RecommendationsPanel } from "@/components/recommendations/RecommendationsPanel";
import { buildRecommendations, type Recommendation } from "@/lib/recommendations";
import { confidenceTone, cn, formatNumber, statusTone } from "@/lib/utils";
import type {
  Conversion,
  DatasetDetail,
  FBDIField,
  MappingSuggestion,
} from "@/types";

type FilterMode = "all" | "required" | "review" | "approved" | "unmapped";

export const MappingReviewPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");

  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<number | null>(projParam ? Number(projParam) : null);

  const [project, setProject] = useState<Conversion | null>(null);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [targetFields, setTargetFields] = useState<FBDIField[]>([]);
  const [mappings, setMappings] = useState<MappingSuggestion[]>([]);

  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<FilterMode>("all");
  const [search, setSearch] = useState("");
  const [selectedMappingId, setSelectedMappingId] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showRecs, setShowRecs] = useState(true);

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

  // Load project context
  const loadAll = async () => {
    if (!pid) return;
    setMappings([]);
    const proj = await ConversionsApi.get(pid);
    setProject(proj);
    if (!proj.dataset_id || !proj.template_id) {
      // Conversion is in planning — nothing to map yet
      setDataset(null);
      setTargetFields([]);
      return;
    }
    const [ds, fields, ms] = await Promise.all([
      DatasetsApi.get(proj.dataset_id),
      FbdiApi.fields(proj.template_id),
      MappingApi.list(pid),
    ]);
    setDataset(ds);
    setTargetFields(fields);
    setMappings(ms);
  };
  useEffect(() => { loadAll(); }, [pid]);

  // Run AI mapping
  const suggest = async () => {
    if (!pid) return;
    setRunning(true);
    try {
      const res = await MappingApi.suggest(pid);
      setMappings(res);
      const auto = res.filter((m) => m.approved_by === "learning-engine").length;
      flash(
        auto
          ? `AI mapping run complete — ${auto} auto-applied from learning library`
          : "AI mapping run complete"
      );
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
        default: return true;
      }
    });
  }, [mappings, search, filter]);

  const visibleTargetIds = useMemo(() => new Set(visibleMappings.map((m) => m.target_field_id)), [visibleMappings]);

  // ── Stats ──
  const stats = useMemo(() => {
    const total = mappings.length;
    const mapped = mappings.filter((m) => m.source_column).length;
    const approved = mappings.filter((m) => m.status === "approved").length;
    const reqMissing = mappings.filter((m) => m.target_required && !m.source_column && m.status !== "approved").length;
    const learned = mappings.filter(
      (m) => m.status === "approved" &&
        (m.approved_by === "learning-engine" || m.comment?.includes("[learned]"))
    ).length;
    return { total, mapped, approved, reqMissing, learned };
  }, [mappings]);

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

  if (!pid || !project || !dataset) return <PageLoader />;

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-canvas">
      {/* Top bar */}
      <header className="border-b border-line bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <ArrowLeftRight className="h-4 w-4 text-brand" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-ink">Mapping Review</div>
            <div className="text-[11px] text-ink-muted">
              <span className="text-ink">{dataset.name}</span>
              <span className="mx-1.5">→</span>
              <span className="text-ink">{project.template_name}</span>
              <span className="ml-1.5 font-mono text-ink-subtle">· {targetFields.length} target fields</span>
            </div>
          </div>

          <select
            className="input !h-8 !w-auto !text-xs"
            value={pid ?? ""}
            onChange={(e) => { const v = Number(e.target.value); setPid(v); setParams({ conversion: String(v) }); }}
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

        {/* Stats + filters */}
        <div className="mt-3 flex items-center gap-3">
          <Stat label="Target fields"  value={stats.total} />
          <Stat label="Auto-mapped"    value={stats.mapped}    tone="info" />
          <Stat label="Approved"       value={stats.approved}  tone="success" />
          <Stat label="Required gaps"  value={stats.reqMissing} tone="danger" />
          <Stat label="Learned"        value={stats.learned}   tone="brand" />

          <div className="flex-1" />

          <div className="flex items-center rounded-md border border-line bg-white p-0.5">
            {([
              { v: "all",      label: "All" },
              { v: "required", label: "Required" },
              { v: "review",   label: "Needs review" },
              { v: "approved", label: "Approved" },
              { v: "unmapped", label: "Required gaps" },
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

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Mapping canvas */}
        <MappingCanvas
          sourceColumns={dataset.columns}
          targetFields={targetFields}
          mappings={mappings}
          visibleTargetIds={visibleTargetIds}
          selectedMappingId={selectedMappingId}
          setSelectedMappingId={setSelectedMappingId}
          hoveredSource={hoveredSource}
          setHoveredSource={setHoveredSource}
          hoveredTarget={hoveredTarget}
          setHoveredTarget={setHoveredTarget}
          loading={running}
        />

        {/* Selected mapping inspector */}
        {selectedMapping && (
          <MappingInspector
            mapping={selectedMapping}
            sourceColumns={dataset.columns}
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
            recommendations={recommendations}
            onApply={() => flash("Applied")}
            onDismiss={() => {}}
            className="w-[340px]"
          />
        )}
      </div>

      {toast && (
        <div className="pointer-events-none fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}

      {pid && dataset && (
        <RuleAuthorModal
          open={ruleAuthorOpen}
          onClose={() => setRuleAuthorOpen(false)}
          conversionId={pid}
          fields={targetFields}
          sourceColumns={dataset.columns}
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
  loading?: boolean;
}

const MappingCanvas: React.FC<CanvasProps> = ({
  sourceColumns, targetFields, mappings, visibleTargetIds,
  selectedMappingId, setSelectedMappingId,
  hoveredSource, setHoveredSource, hoveredTarget, setHoveredTarget, loading,
}) => {
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
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Source · {sortedSources.length}</div>
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
                onClick={() => mapping && setSelectedMappingId(mapping.id)}
                onMouseEnter={() => setHoveredSource(c.column_name)}
                onMouseLeave={() => setHoveredSource(null)}
                className={cn(
                  "mb-1 cursor-pointer rounded-md border bg-white px-2.5 py-2 transition",
                  hoveredSource === c.column_name ? "border-brand bg-brand-subtle/40 shadow-soft" :
                  isMapped ? "border-line" : "border-line/60 opacity-80",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12px] font-semibold text-ink">{c.column_name}</div>
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
                className={cn(
                  "mb-1 cursor-pointer rounded-md border bg-white px-2.5 py-2 transition",
                  !visible && "opacity-30",
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
              <div className="font-mono text-[12px] text-ink">{mapping.source_column || "— (none)"}</div>
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

        {/* Confidence */}
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">AI confidence</span>
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
            <Button
              onClick={() => onApprove(mapping)}
              className="!h-8 !bg-brand-dark hover:!bg-brand"
            >
              <GraduationCap className="h-3.5 w-3.5" /> Approve &amp; Learn
            </Button>
          </div>
        )}
      </div>
    </aside>
  );
};
