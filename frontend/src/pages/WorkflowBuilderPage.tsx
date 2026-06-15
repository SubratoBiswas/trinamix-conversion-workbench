import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import ReactFlow, {
  Background, Controls, addEdge, useNodesState, useEdgesState, Edge, Node,
  Connection, MarkerType, ReactFlowProvider, useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  ArrowLeft, Save, Play, MoreHorizontal, Workflow as WfIcon,
  Database, FileSpreadsheet, ArrowRight, ExternalLink,
} from "lucide-react";
import { ConversionsApi, ProjectsApi, WorkflowApi, DatasetsApi, FbdiApi } from "@/api";
import { Button, PageLoader, Pill } from "@/components/ui/Primitives";
import { DataflowNode } from "@/components/workflow/DataflowNode";
import { DataflowPalette } from "@/components/workflow/DataflowPalette";
import { DataflowToolbar } from "@/components/workflow/DataflowToolbar";
import { PropertiesPanel } from "@/components/workflow/PropertiesPanel";
import { defaultNodeData, NODE_TYPES } from "@/components/workflow/NodeRegistry";
import { autoLayout } from "@/components/workflow/autoLayout";
import { cn } from "@/lib/utils";
import type { ConversionProject, Dataset, FBDITemplate, Workflow } from "@/types";

const nodeTypes = { dataflow: DataflowNode };

// Edge style — clean grey arrow, becomes brand-coloured when selected
const EDGE_STYLE_DEFAULT = { stroke: "#94A3B8", strokeWidth: 1.5 };
const EDGE_STYLE_SELECTED = { stroke: "#6366F1", strokeWidth: 2 };

export const WorkflowBuilderPage: React.FC = () => (
  <ReactFlowProvider>
    <Inner />
  </ReactFlowProvider>
);

const Inner: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const wid = Number(id);
  const nav = useNavigate();

  // ── Workflow + supporting catalogues ──
  const [wf, setWf] = useState<Workflow | null>(null);
  const [projects, setProjects] = useState<ConversionProject[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);

  // ── Canvas state ──
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // ── UI prefs ──
  const [paletteTab, setPaletteTab] = useState<"actions" | "datasets">("actions");
  const [showLabels, setShowLabels] = useState(true);
  const [layout, setLayout] = useState<"horizontal" | "vertical">("horizontal");
  const [propertiesOpen, setPropertiesOpen] = useState(true);

  // ── Status ──
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const wrapRef = useRef<HTMLDivElement>(null);
  const { project: rfProject, fitView } = useReactFlow();

  // ── Initial load ──
  useEffect(() => {
    if (!wid) return;
    WorkflowApi.get(wid).then((w) => {
      setWf(w);
      const ns: Node[] = (w.nodes || []).map((n: any) => ({
        ...n,
        type: "dataflow",
        data: { ...defaultNodeData(n.data?.nodeType || n.type || "dataset"), ...n.data },
      }));
      setNodes(ns);
      setEdges((w.edges || []).map((e: any) => ({
        ...e,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
        style: EDGE_STYLE_DEFAULT,
        label: showLabels ? "" : undefined,
      })));
      // First render: fit
      setTimeout(() => fitView({ padding: 0.2, duration: 250 }), 100);
    });
    ConversionsApi.list().then(setProjects);
    DatasetsApi.list().then(setDatasets);
    FbdiApi.list().then(setTemplates);
  }, [wid]);

  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 2400); };

  // ── Handlers ──
  const onConnect = useCallback((c: Connection) => setEdges((eds) =>
    addEdge({
      ...c,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
      style: EDGE_STYLE_DEFAULT,
    }, eds)
  ), [setEdges]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const typeKey = e.dataTransfer.getData("application/trinamix-node");
    if (!typeKey || !wrapRef.current) return;
    const bounds = wrapRef.current.getBoundingClientRect();
    const position = rfProject({ x: e.clientX - bounds.left, y: e.clientY - bounds.top });
    const id = `${typeKey}_${Date.now()}`;
    const def = NODE_TYPES[typeKey];
    setNodes((ns) => [...ns, {
      id,
      type: "dataflow",
      position,
      data: defaultNodeData(typeKey),
    }]);
    setSelectedId(id);
    setPropertiesOpen(true);
  }, [rfProject, setNodes]);

  const onNodeChange = useCallback((nodeId: string, patch: Record<string, any>) => {
    setNodes((ns) => ns.map((n) => {
      if (n.id !== nodeId) return n;
      const newData = { ...n.data, ...patch };
      // Sync the canvas detail line with key config values, resolving IDs to names.
      newData.detail = computeDetail(newData, { datasets, templates });
      return { ...n, data: newData };
    }));
  }, [setNodes, datasets, templates]);

  // Whenever the catalog loads or auto-bind populates IDs, refresh node details
  useEffect(() => {
    if (datasets.length === 0 && templates.length === 0) return;
    setNodes((ns) => ns.map((n) => ({
      ...n,
      data: { ...n.data, detail: computeDetail(n.data, { datasets, templates }) || n.data?.detail },
    })));
  }, [datasets, templates]);

  const onDeleteNode = useCallback((nodeId: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== nodeId));
    setEdges((es) => es.filter((e) => e.source !== nodeId && e.target !== nodeId));
    if (selectedId === nodeId) setSelectedId(null);
  }, [setNodes, setEdges, selectedId]);

  const triggerAutoLayout = useCallback(() => {
    setNodes((ns) => autoLayout(ns, edges, layout));
    setTimeout(() => fitView({ padding: 0.2, duration: 250 }), 50);
  }, [edges, layout, setNodes, fitView]);

  const save = async () => {
    setSaving(true);
    try {
      const cleanNodes = nodes.map(({ id, type, position, data }) => ({ id, type, position, data }));
      const cleanEdges = edges.map(({ id, source, target, sourceHandle, targetHandle }) =>
        ({ id, source, target, sourceHandle, targetHandle }));
      const updated = await WorkflowApi.update(wid, { nodes: cleanNodes, edges: cleanEdges, status: "saved" });
      setWf(updated);
      flash("Dataflow saved");
    } finally { setSaving(false); }
  };

  const run = async () => {
    setRunning(true);
    try {
      await save();
      const ran = await WorkflowApi.run(wid);
      setWf(ran);
      const stepByNodeId = new Map<string, any>();
      (ran.last_run_summary?.steps || []).forEach((s: any) => stepByNodeId.set(s.node_id, s));

      setNodes((ns) => ns.map((n) => {
        const step = stepByNodeId.get(n.id);
        // For nodes whose configured detail is meaningful (e.g. Dataset name,
        // Template name), keep that; for runnable nodes, prefer the run's detail.
        const ntype = n.data?.nodeType;
        const isConfigDriven = ntype === "dataset" || ntype === "fbdi_template";
        return {
          ...n,
          data: {
            ...n.data,
            runStatus: step?.status,
            detail: isConfigDriven
              ? (n.data?.detail || step?.detail)            // keep configured name on data sources
              : (step?.detail || n.data?.detail),           // show run output on AI/validate/etc.
          },
        };
      }));

      // Auto-route to Mapping Review when an AI Auto Map step completed successfully
      const aiStep = (ran.last_run_summary?.steps || []).find(
        (s: any) => s.type === "ai_auto_map" && s.status === "ok"
      );
      if (aiStep && ran.conversion_id) {
        flash("AI mapping complete — opening Mapping Review…");
        setTimeout(() => nav(`/mappings?project=${ran.conversion_id}`), 1200);
      } else {
        flash(`Run ${ran.status}`);
      }
    } finally { setRunning(false); }
  };

  const updateProject = async (newPid: number | null) => {
    const updated = await WorkflowApi.update(wid, { conversion_id: newPid });
    setWf(updated);

    if (newPid) {
      // Auto-populate any unconfigured Dataset / FBDI Target nodes with the
      // project's bindings so the canvas reflects what's actually wired.
      const proj = projects.find((p) => p.id === newPid);
      if (proj) {
        setNodes((ns) => ns.map((n) => {
          if (n.data?.nodeType === "dataset" && !n.data?.datasetId) {
            return { ...n, data: { ...n.data, datasetId: proj.dataset_id } };
          }
          if (n.data?.nodeType === "fbdi_template" && !n.data?.templateId) {
            return { ...n, data: { ...n.data, templateId: proj.template_id } };
          }
          return n;
        }));
      }
    }
  };

  // ── Resolve upstream dataset for the selected node (used by data-preview tab) ──
  const upstreamDatasetId = useMemo(() => {
    if (!selectedId) return null;
    // BFS upstream looking for a dataset node
    const incoming = new Map<string, string[]>();
    for (const e of edges) {
      incoming.set(e.target, [...(incoming.get(e.target) || []), e.source]);
    }
    const visited = new Set<string>();
    const q: string[] = [selectedId];
    while (q.length) {
      const cur = q.shift()!;
      if (visited.has(cur)) continue;
      visited.add(cur);
      const node = nodes.find((n) => n.id === cur);
      if (node?.data?.nodeType === "dataset" && node.data?.datasetId) {
        return Number(node.data.datasetId);
      }
      for (const src of incoming.get(cur) || []) q.push(src);
    }
    return null;
  }, [selectedId, edges, nodes]);

  const selectedNode = nodes.find((n) => n.id === selectedId) || null;

  // Edge labels reflect the showLabels pref
  const renderedEdges = useMemo(() => edges.map((e) => ({
    ...e,
    label: showLabels && e.data?.label ? e.data.label : "",
    style: e.selected ? EDGE_STYLE_SELECTED : EDGE_STYLE_DEFAULT,
  })), [edges, showLabels]);

  if (!wf) return <PageLoader />;

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-canvas">
      {/* Top header — workflow name, project binding, actions */}
      <header className="flex items-center gap-3 border-b border-line bg-white px-5 py-2.5">
        <Link to="/workflows" className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <WfIcon className="h-4 w-4 text-ink-muted" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-ink">{wf.name}</div>
          <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
            <span>Project:</span>
            <select
              value={wf.conversion_id ?? ""}
              onChange={(e) => updateProject(e.target.value ? Number(e.target.value) : null)}
              className="rounded border border-line bg-white px-1.5 py-0.5 text-[11px] text-ink hover:border-brand"
            >
              <option value="">— none —</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
        <Pill tone={
          wf.status === "success" ? "success" :
          wf.status === "failed" ? "danger" :
          wf.status === "running" ? "warning" : "neutral"
        }>{wf.status}</Pill>
        <Button variant="secondary" onClick={save} loading={saving}>
          <Save className="h-4 w-4" /> Save
        </Button>
        <Button onClick={run} loading={running} disabled={!wf.conversion_id}>
          <Play className="h-4 w-4" /> Run
        </Button>
      </header>

      {/* Project context banner — surfaces the binding above-the-fold */}
      <ProjectContextBanner
        wf={wf}
        projects={projects}
        datasets={datasets}
        templates={templates}
        onOpenMappings={() => nav(`/mappings?project=${wf.conversion_id}`)}
      />

      {/* Main 3-column layout */}
      <div className="flex flex-1 overflow-hidden">
        <DataflowPalette tab={paletteTab} setTab={setPaletteTab} />

        <div className="flex flex-1 flex-col overflow-hidden">
          <DataflowToolbar
            showLabels={showLabels} setShowLabels={setShowLabels}
            layout={layout} setLayout={setLayout}
            onAutoLayout={triggerAutoLayout}
          />

          <div
            ref={wrapRef}
            className="relative flex-1 overflow-hidden"
            onDrop={onDrop}
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
          >
            <ReactFlow
              nodes={nodes}
              edges={renderedEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, n) => { setSelectedId(n.id); setPropertiesOpen(true); }}
              onPaneClick={() => setSelectedId(null)}
              nodeTypes={nodeTypes}
              fitView
              proOptions={{ hideAttribution: true }}
              defaultEdgeOptions={{
                markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
                style: EDGE_STYLE_DEFAULT,
              }}
            >
              <Background color="#CBD5E1" gap={20} size={1} />
              <Controls className="!shadow-card" showInteractive={false} />
            </ReactFlow>

            {/* Empty-state overlay */}
            {nodes.length === 0 && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className="rounded-lg border-2 border-dashed border-line bg-white/80 px-8 py-6 text-center">
                  <WfIcon className="mx-auto mb-2 h-8 w-8 text-brand-light" />
                  <div className="text-sm font-semibold text-ink">Empty canvas</div>
                  <div className="mt-1 text-xs text-ink-muted">
                    Drag <span className="font-mono text-brand-dark">Add Data</span> from the palette to get started.
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Bottom properties panel */}
          {propertiesOpen && (
            <div
              className="border-t border-line bg-canvas"
              style={{ height: 320 }}
            >
              <PropertiesPanel
                node={selectedNode}
                onClose={() => setSelectedId(null)}
                onChange={onNodeChange}
                onDelete={onDeleteNode}
                upstreamDatasetId={upstreamDatasetId}
                datasets={datasets}
                templates={templates}
              />
            </div>
          )}
        </div>
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}
    </div>
  );
};

/** Render a short summary line shown under the node label on the canvas. */
function computeDetail(
  data: Record<string, any>,
  catalog?: { datasets: Dataset[]; templates: FBDITemplate[] }
): string | undefined {
  const t = data.nodeType;
  switch (t) {
    case "dataset": {
      if (!data.datasetId) return "no dataset";
      const ds = catalog?.datasets.find((d) => d.id === Number(data.datasetId));
      return ds ? ds.name : `Dataset #${data.datasetId}`;
    }
    case "fbdi_template": {
      if (!data.templateId) return "no template";
      const tpl = catalog?.templates.find((x) => x.id === Number(data.templateId));
      return tpl ? tpl.name : `Template #${data.templateId}`;
    }
    case "transform":      return data.column ? `${data.ruleType} on ${data.column}` : data.ruleType;
    case "filter":         return data.expression ? truncate(String(data.expression), 24) : undefined;
    case "join":           return data.leftKey && data.rightKey ? `${data.leftKey} ⨝ ${data.rightKey}` : undefined;
    case "aggregate":      return data.agg ? `${data.agg}(...)` : undefined;
    case "select_columns": return Array.isArray(data.columns) ? `${data.columns.length} col(s)` : undefined;
    case "ai_auto_map":    return `min conf ${Math.round((data.minConfidence ?? 0.2) * 100)}%`;
    case "load_to_fusion": return data.mode || undefined;
    case "generate_fbdi":  return (data.format || "csv").toUpperCase();
  }
  return undefined;
}

function truncate(s: string, n: number) {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

/**
 * Surfaces the dataflow's source dataset → target FBDI binding above the canvas
 * so the user always knows what the run will operate against.
 */
const ProjectContextBanner: React.FC<{
  wf: Workflow;
  projects: ConversionProject[];
  datasets: Dataset[];
  templates: FBDITemplate[];
  onOpenMappings: () => void;
}> = ({ wf, projects, datasets, templates, onOpenMappings }) => {
  if (!wf.conversion_id) {
    return (
      <div className="border-b border-line bg-warning-subtle/40 px-5 py-2 text-[12px] text-ink">
        <span className="font-semibold text-warning">No project bound.</span>
        <span className="ml-1.5 text-ink-muted">
          Pick a project above to define the source dataset and target FBDI for this dataflow,
          or drag <span className="font-mono">Add Data</span> + <span className="font-mono">Select Target FBDI</span> nodes onto the canvas.
        </span>
      </div>
    );
  }
  const proj = projects.find((p) => p.id === wf.conversion_id);
  if (!proj) return null;
  const ds = datasets.find((d) => d.id === proj.dataset_id);
  const tpl = templates.find((t) => t.id === proj.template_id);

  return (
    <div className="flex items-center gap-3 border-b border-line bg-gradient-to-r from-brand-subtle/40 via-white to-canvas px-5 py-2">
      <div className="flex items-center gap-2">
        <Database className="h-3.5 w-3.5 text-emerald-600" />
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Source</div>
          <div className="text-[12.5px] font-medium text-ink">
            {ds?.name || "—"}
            {ds && (
              <span className="ml-1.5 font-mono text-[10px] text-ink-muted">
                {ds.row_count.toLocaleString()} × {ds.column_count}
              </span>
            )}
          </div>
        </div>
      </div>

      <ArrowRight className="h-3.5 w-3.5 text-ink-subtle" />

      <div className="flex items-center gap-2">
        <FileSpreadsheet className="h-3.5 w-3.5 text-indigo-600" />
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Target FBDI</div>
          <div className="text-[12.5px] font-medium text-ink">
            {tpl?.name || "—"}
            {tpl && (
              <span className="ml-1.5 font-mono text-[10px] text-ink-muted">
                {tpl.business_object || ""}{tpl.module ? ` · ${tpl.module}` : ""}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1" />

      <button
        onClick={onOpenMappings}
        className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2.5 py-1.5 text-[11px] font-medium text-ink hover:border-brand hover:bg-brand-subtle hover:text-brand-dark"
        title="Open Mapping Review for this project"
      >
        <ExternalLink className="h-3 w-3" /> Open Mapping Review
      </button>
    </div>
  );
};
