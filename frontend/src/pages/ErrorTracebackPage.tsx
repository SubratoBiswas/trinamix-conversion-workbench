import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactFlow, {
  Background, Controls, Edge, Node, useEdgesState, useNodesState, MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  AlertTriangle, Network, ArrowLeftRight, AlertCircle, ChevronRight,
  X, Database, Link2, ArrowDown, FileX,
} from "lucide-react";
import { ConversionsApi, DependencyApi, LoadApi } from "@/api";
import {
  Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  Conversion,
  Dependency,
  LoadError,
  LoadSummary,
} from "@/types";

interface TracebackNode extends Node {
  data: { label: string; subtitle?: string; tone: "ok" | "failed" | "missing" | "warning"; count?: number };
}

const TONE_STYLES: Record<string, { bg: string; border: string; text: string }> = {
  ok:       { bg: "#FFFFFF", border: "#10B981", text: "#0F172A" },
  failed:   { bg: "#FEF2F2", border: "#EF4444", text: "#7F1D1D" },
  missing:  { bg: "#FFFBEB", border: "#F59E0B", text: "#78350F" },
  warning:  { bg: "#FFFBEB", border: "#F59E0B", text: "#78350F" },
};

/**
 * Renders the dependency graph with post-load error arrows. Each failure that
 * the load simulator categorised with a `related_dependency` becomes a red,
 * thick, labelled arrow from the failed object node BACK to the upstream
 * master-data object that caused it.
 */
export const ErrorTracebackPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");

  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<number | null>(projParam ? Number(projParam) : null);
  const [project, setProject] = useState<Conversion | null>(null);
  const [deps, setDeps] = useState<Dependency[]>([]);
  const [summary, setSummary] = useState<LoadSummary | null>(null);
  const [errors, setErrors] = useState<LoadError[]>([]);
  const [drawerObject, setDrawerObject] = useState<string | null>(null);

  useEffect(() => {
    ConversionsApi.list().then((ps) => {
      setProjects(ps);
      if (!pid && ps[0]) {
        setPid(ps[0].id);
        setParams({ conversion: String(ps[0].id) });
      }
    });
    DependencyApi.list().then(setDeps);
  }, []);

  useEffect(() => {
    if (!pid) return;
    ConversionsApi.get(pid).then(setProject);
    LoadApi.summary(pid).then(setSummary).catch(() => setSummary(null));
    LoadApi.latestErrors(pid).then(setErrors).catch(() => setErrors([]));
  }, [pid]);

  // Build the dependency graph + paint the load summary on top
  const targetObject = project?.template_name ?
    project.template_name.split(" ")[0] : "Item";

  const { nodes, edges } = useMemo(() =>
    buildTracebackGraph(deps, targetObject, summary),
    [deps, targetObject, summary]
  );

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<any>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<any>([]);
  useEffect(() => { setRfNodes(nodes); setRfEdges(edges); }, [nodes, edges]);

  if (!pid || !project) return <PageLoader />;

  const failedTotal = summary?.failed_count || 0;
  const causes = summary?.dependency_impacts || [];

  return (
    <>
      <PageTitle
        title="Error Traceback"
        subtitle="Post-load failures traced back through the dependency graph to upstream master objects"
        right={
          <select className="input !w-auto !min-w-[280px]" value={pid ?? ""}
            onChange={(e) => { const v = Number(e.target.value); setPid(v); setParams({ conversion: String(v) }); }}>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        }
      />

      {!summary || summary.total_records === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon={<AlertTriangle className="h-5 w-5" />}
              title="No load simulation yet"
              description="Run a load simulation from the Load Runs page — failures will be traced back to upstream master objects here."
            />
          </CardBody>
        </Card>
      ) : (
        <>
          {/* Top KPI */}
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <KpiTile label="Total records" value={summary.total_records.toLocaleString()} />
            <KpiTile label="Failed" value={summary.failed_count.toLocaleString()} tone="text-danger" />
            <KpiTile label="Missing-dependency failures"
              value={causes.reduce((s, c) => s + c.count, 0).toLocaleString()}
              tone="text-warning" />
            <KpiTile label="Upstream objects implicated" value={causes.length} tone="text-info" />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* Graph */}
            <Card className="lg:col-span-2">
              <CardHeader
                title={<><Network className="mr-2 inline h-4 w-4 text-brand" />Dependency Graph · Post-load Traceback</>}
                subtitle="Red arrows show which upstream master objects caused each downstream failure"
              />
              <div className="h-[520px] w-full">
                <ReactFlow
                  nodes={rfNodes}
                  edges={rfEdges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  fitView
                  fitViewOptions={{ padding: 0.25 }}
                  proOptions={{ hideAttribution: true }}
                  minZoom={0.4}
                  maxZoom={1.5}
                  nodesDraggable
                  nodesConnectable={false}
                >
                  <Background color="#E2E8F0" gap={20} />
                  <Controls className="!shadow-card" showInteractive={false} />
                </ReactFlow>
              </div>
            </Card>

            {/* Failure breakdown side panel */}
            <Card>
              <CardHeader title="Failure Causes" subtitle={`${failedTotal.toLocaleString()} failed records`} />
              <div className="px-5 py-4">
                {causes.length === 0 ? (
                  <div className="text-xs text-ink-muted">No dependency-related failures recorded.</div>
                ) : (
                  <div className="space-y-2">
                    {causes.map((c) => (
                      <button
                        key={c.object}
                        onClick={() => setDrawerObject(c.object)}
                        className="group block w-full rounded-md border border-danger/30 bg-danger-subtle/40 px-3 py-2 text-left transition hover:border-danger hover:bg-danger-subtle/70"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5 text-sm font-semibold text-danger">
                            <AlertCircle className="h-3.5 w-3.5" />
                            {c.object} missing
                          </div>
                          <span className="font-mono text-sm font-bold tabular-nums text-danger">{c.count}</span>
                        </div>
                        <div className="mt-1 text-[11.5px] leading-snug text-ink-muted">
                          {c.count} {targetObject} record{c.count === 1 ? "" : "s"} failed because the
                          upstream <span className="font-semibold text-ink">{c.object}</span> master row
                          could not be resolved.
                        </div>
                        <div className="mt-2 flex items-center gap-1.5 text-[10.5px] font-medium text-brand-dark">
                          <ChevronRight className="h-3 w-3 transition group-hover:translate-x-0.5" />
                          See the connection path
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          </div>

          {drawerObject && project && (
            <FailureChainDrawer
              object={drawerObject}
              targetObject={targetObject}
              currentConversionName={project.name}
              errors={errors.filter(
                (e) => e.related_dependency === drawerObject && e.error_category === "Missing Dependency"
              )}
              onClose={() => setDrawerObject(null)}
            />
          )}

          {/* Legend */}
          <Card className="mt-4">
            <CardBody>
              <div className="flex flex-wrap items-center gap-4 text-[11.5px] text-ink-muted">
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-3 w-3 rounded-sm border-2" style={{ borderColor: "#10B981" }} />
                  Loaded successfully
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-3 w-3 rounded-sm border-2" style={{ borderColor: "#F59E0B" }} />
                  Upstream master implicated in failures
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-3 w-3 rounded-sm border-2" style={{ borderColor: "#EF4444" }} />
                  Failed object (current load target)
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-6" style={{ backgroundColor: "#94A3B8" }} />
                  Normal prerequisite
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-6" style={{ backgroundColor: "#EF4444" }} />
                  Error traceback (count of failures)
                </div>
              </div>
            </CardBody>
          </Card>
        </>
      )}
    </>
  );
};

// ─────── Failure chain drawer ───────
//
// Renders the visual connection path that explains *why* a downstream load
// failed: the failed conversion → the reference column it leans on → the
// upstream master where the lookup didn't resolve.

const FailureChainDrawer: React.FC<{
  object: string;
  targetObject: string;
  currentConversionName: string;
  errors: LoadError[];
  onClose: () => void;
}> = ({ object, targetObject, currentConversionName, errors, onClose }) => {
  const refField = errors[0]?.object_name ?? "—";
  const uniqueRefs = useMemo(() => {
    const map = new Map<string, number[]>();
    for (const e of errors) {
      const k = e.reference_value || "(blank)";
      if (!map.has(k)) map.set(k, []);
      if (e.row_number != null) map.get(k)!.push(e.row_number);
    }
    return Array.from(map.entries())
      .map(([key, rows]) => ({ key, rows, count: rows.length }))
      .sort((a, b) => b.count - a.count);
  }, [errors]);

  const sample = errors[0];

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-ink/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-full max-w-[520px] flex-col bg-white shadow-soft"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div>
            <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              Failure Chain · {targetObject} → {object}
            </div>
            <div className="text-sm font-semibold text-ink">
              {errors.length} record{errors.length === 1 ? "" : "s"} blocked by{" "}
              <span className="text-danger">missing {object}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {/* Visual connection chain */}
          <div className="mx-auto max-w-md">
            <ChainNode
              tone="failed"
              icon={<FileX className="h-4 w-4" />}
              kicker="Downstream conversion (this load)"
              title={currentConversionName}
              subtitle={`${errors.length} ${targetObject} row${
                errors.length === 1 ? "" : "s"
              } failed to load`}
            />

            <ChainArrow
              label={
                <>
                  references via{" "}
                  <code className="rounded bg-canvas px-1 py-0.5 font-mono text-[11px] text-ink">
                    {refField}
                  </code>
                </>
              }
            />

            <ChainNode
              tone="link"
              icon={<Link2 className="h-4 w-4" />}
              kicker="Foreign key column"
              title={refField}
              subtitle={
                sample
                  ? `e.g. row ${sample.row_number} → "${sample.reference_value ?? ""}"`
                  : undefined
              }
              mono
            />

            <ChainArrow
              label={
                <span className="inline-flex items-center gap-1 text-danger">
                  <AlertCircle className="h-3 w-3" /> no matching record
                </span>
              }
              tone="danger"
            />

            <ChainNode
              tone="missing"
              icon={<Database className="h-4 w-4" />}
              kicker="Upstream master"
              title={`${object} Master`}
              subtitle={`The ${object} extract has no record for these keys`}
            />
          </div>

          {/* Failed reference values */}
          <section className="mt-7">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                Unresolved {object} keys
              </span>
              <Pill tone="danger">{uniqueRefs.length}</Pill>
            </div>
            <div className="rounded-md border border-line bg-canvas">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
                    <th className="px-3 py-2 font-medium">{object} key</th>
                    <th className="px-3 py-2 font-medium">Failed rows</th>
                    <th className="px-3 py-2 font-medium">Hits</th>
                  </tr>
                </thead>
                <tbody>
                  {uniqueRefs.slice(0, 12).map((r) => (
                    <tr key={r.key} className="border-t border-line/60 align-top">
                      <td className="px-3 py-2 font-mono text-danger">{r.key}</td>
                      <td className="px-3 py-2 font-mono text-[11px] text-ink-muted">
                        {r.rows.slice(0, 6).join(", ")}
                        {r.rows.length > 6 ? "…" : ""}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-ink">
                        {r.count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {uniqueRefs.length > 12 && (
                <div className="border-t border-line/60 px-3 py-1.5 text-center text-[11px] text-ink-muted">
                  + {uniqueRefs.length - 12} more
                </div>
              )}
            </div>
          </section>

          {/* Suggested fix */}
          {sample?.suggested_fix && (
            <section className="mt-5 rounded-md bg-info-subtle/60 px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-info">
                <ArrowLeftRight className="h-3 w-3" /> Suggested fix
              </div>
              <div className="mt-1 text-[12px] leading-snug text-ink">
                {sample.suggested_fix}
              </div>
            </section>
          )}
        </div>
      </aside>
    </div>
  );
};

const ChainNode: React.FC<{
  tone: "failed" | "link" | "missing";
  icon: React.ReactNode;
  kicker: string;
  title: string;
  subtitle?: React.ReactNode;
  mono?: boolean;
}> = ({ tone, icon, kicker, title, subtitle, mono }) => {
  const ring = {
    failed: "border-danger ring-danger/15 bg-danger-subtle/40",
    link: "border-line ring-line/30 bg-canvas",
    missing: "border-warning ring-warning/15 bg-warning-subtle/50",
  }[tone];
  const iconWrap = {
    failed: "bg-danger text-white",
    link: "bg-ink text-white",
    missing: "bg-warning text-white",
  }[tone];
  return (
    <div className={cn("rounded-lg border-2 ring-4 px-4 py-3", ring)}>
      <div className="flex items-start gap-3">
        <div className={cn("flex h-7 w-7 items-center justify-center rounded-md", iconWrap)}>
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
            {kicker}
          </div>
          <div className={cn("truncate text-sm font-semibold text-ink", mono && "font-mono text-[13px]")}>
            {title}
          </div>
          {subtitle && (
            <div className="mt-0.5 text-[11px] text-ink-muted">{subtitle}</div>
          )}
        </div>
      </div>
    </div>
  );
};

const ChainArrow: React.FC<{ label?: React.ReactNode; tone?: "neutral" | "danger" }> = ({
  label,
  tone = "neutral",
}) => (
  <div className="flex items-center gap-2 py-2 pl-7">
    <div
      className={cn(
        "flex h-6 w-6 items-center justify-center",
        tone === "danger" ? "text-danger" : "text-ink-muted"
      )}
    >
      <ArrowDown className="h-4 w-4" />
    </div>
    {label && (
      <div className={cn("text-[11px]", tone === "danger" ? "text-danger" : "text-ink-muted")}>
        {label}
      </div>
    )}
  </div>
);

const KpiTile: React.FC<{ label: string; value: React.ReactNode; tone?: string }> = ({ label, value, tone }) => (
  <div className="card p-3">
    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">{label}</div>
    <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone || "text-ink")}>{value}</div>
  </div>
);

// ─────── Graph builder ───────

function buildTracebackGraph(
  deps: Dependency[],
  targetObject: string,
  summary: LoadSummary | null,
): { nodes: TracebackNode[]; edges: Edge[] } {
  const objects = new Set<string>();
  deps.forEach(d => { objects.add(d.source_object); objects.add(d.target_object); });
  if (targetObject) objects.add(targetObject);

  // Compute incoming/outgoing
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const d of deps) {
    incoming.set(d.target_object, [...(incoming.get(d.target_object) || []), d.source_object]);
    outgoing.set(d.source_object, [...(outgoing.get(d.source_object) || []), d.target_object]);
  }

  // BFS depth from any object that doesn't appear as target (= roots)
  const roots = Array.from(objects).filter(o => !(incoming.get(o)?.length));
  const depth = new Map<string, number>();
  const q: [string, number][] = roots.map(r => [r, 0]);
  while (q.length) {
    const [n, d] = q.shift()!;
    if ((depth.get(n) || -1) >= d) continue;
    depth.set(n, d);
    for (const next of outgoing.get(n) || []) q.push([next, d + 1]);
  }

  // Map: object name → impact count from load summary
  const impactByObject = new Map<string, number>();
  for (const i of summary?.dependency_impacts || []) {
    impactByObject.set(i.object, i.count);
  }
  const failedTotal = summary?.failed_count || 0;
  const targetIsFailed = (summary?.failed_count || 0) > 0;

  // Group by depth for layout
  const byDepth = new Map<number, string[]>();
  for (const o of objects) {
    const d = depth.get(o) ?? 0;
    byDepth.set(d, [...(byDepth.get(d) || []), o]);
  }

  // Nodes
  const COL_W = 220, ROW_H = 110, OFFSET_X = 60, OFFSET_Y = 60;
  const nodes: TracebackNode[] = [];
  Array.from(byDepth.entries()).sort((a, b) => a[0] - b[0]).forEach(([d, list]) => {
    list.forEach((obj, i) => {
      const isTarget = obj === targetObject;
      const impactCount = impactByObject.get(obj) || 0;
      const tone: "ok" | "failed" | "missing" =
        isTarget && targetIsFailed ? "failed" :
        impactCount > 0 ? "missing" :
        "ok";
      const style = TONE_STYLES[tone];
      nodes.push({
        id: obj,
        position: { x: d * COL_W + OFFSET_X, y: i * ROW_H + OFFSET_Y },
        data: {
          label: obj,
          subtitle: isTarget ? `${failedTotal} failed records` :
            impactCount > 0 ? `${impactCount} downstream failure${impactCount === 1 ? "" : "s"}` :
            undefined,
          tone,
          count: impactCount,
        },
        style: {
          width: 180,
          background: style.bg,
          border: `2px solid ${style.border}`,
          borderRadius: 8,
          boxShadow: tone === "failed" ? "0 0 0 4px rgba(239,68,68,0.18)" :
                     tone === "missing" ? "0 0 0 4px rgba(245,158,11,0.18)" : "0 1px 2px rgba(0,0,0,0.04)",
          padding: "10px 14px",
          fontSize: 13,
          fontWeight: 500,
          color: style.text,
        },
      } as TracebackNode);
    });
  });

  // Edges — normal prerequisites
  const normalEdges: Edge[] = deps.map((d, i) => ({
    id: `norm-${i}`,
    source: d.source_object,
    target: d.target_object,
    type: "smoothstep",
    style: { stroke: "#94A3B8", strokeWidth: 1.25 },
    markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
    label: d.relationship_type,
    labelStyle: { fontSize: 9, fill: "#94A3B8" },
    labelBgStyle: { fill: "#F8FAFC" },
  }));

  // Error traceback arrows — drawn from failed target BACK to each impacted upstream
  const errorEdges: Edge[] = [];
  for (const impact of summary?.dependency_impacts || []) {
    if (!objects.has(impact.object)) continue;
    errorEdges.push({
      id: `err-${impact.object}`,
      source: targetObject,
      target: impact.object,
      type: "smoothstep",
      animated: true,
      style: { stroke: "#EF4444", strokeWidth: 2.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#EF4444" },
      label: `${impact.count} failure${impact.count === 1 ? "" : "s"}`,
      labelStyle: { fontSize: 11, fill: "#EF4444", fontWeight: 600 },
      labelBgStyle: { fill: "#FEF2F2", fillOpacity: 0.95 },
      labelBgPadding: [4, 2] as any,
      labelBgBorderRadius: 4,
    });
  }

  return { nodes, edges: [...normalEdges, ...errorEdges] };
}
