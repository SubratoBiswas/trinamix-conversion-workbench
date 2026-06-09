import React, { useEffect, useState } from "react";
import ReactFlow, {
  Background, Controls, Edge, Node, useEdgesState, useNodesState, MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import { Network } from "lucide-react";
import { DependencyApi } from "@/api";
import { Card, CardHeader, EmptyState, PageLoader, PageTitle } from "@/components/ui/Primitives";
import type { Dependency } from "@/types";

// Layered layout: prerequisite chain L→R
function layout(deps: Dependency[]): { nodes: Node[]; edges: Edge[] } {
  const objects = new Set<string>();
  deps.forEach(d => { objects.add(d.source_object); objects.add(d.target_object); });

  // BFS depth from any object that doesn't appear as a target
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const d of deps) {
    incoming.set(d.target_object, [...(incoming.get(d.target_object) || []), d.source_object]);
    outgoing.set(d.source_object, [...(outgoing.get(d.source_object) || []), d.target_object]);
  }
  const roots = Array.from(objects).filter(o => !(incoming.get(o)?.length));
  const depth = new Map<string, number>();
  const q: [string, number][] = roots.map(r => [r, 0]);
  while (q.length) {
    const [n, d] = q.shift()!;
    if ((depth.get(n) || -1) >= d) continue;
    depth.set(n, d);
    for (const next of outgoing.get(n) || []) q.push([next, d + 1]);
  }
  // Group by depth
  const byDepth = new Map<number, string[]>();
  for (const o of objects) {
    const d = depth.get(o) ?? 0;
    byDepth.set(d, [...(byDepth.get(d) || []), o]);
  }

  const nodes: Node[] = [];
  const COL_W = 220, ROW_H = 90;
  Array.from(byDepth.entries())
    .sort((a, b) => a[0] - b[0])
    .forEach(([d, list]) => {
      list.forEach((obj, i) => {
        nodes.push({
          id: obj,
          position: { x: d * COL_W, y: i * ROW_H + 40 },
          data: { label: obj },
          style: {
            width: 180,
            background: "white",
            border: "1px solid #E2E8F0",
            borderRadius: 8,
            boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
            padding: "10px 14px",
            fontSize: 13,
            fontWeight: 500,
            color: "#0F172A",
          },
        });
      });
    });
  const edges: Edge[] = deps.map((d, i) => ({
    id: `e-${i}`,
    source: d.source_object,
    target: d.target_object,
    type: "smoothstep",
    animated: false,
    style: { stroke: "#94A3B8", strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: "#94A3B8" },
    label: d.relationship_type,
    labelStyle: { fontSize: 10, fill: "#64748B" },
    labelBgStyle: { fill: "#F8FAFC" },
  }));
  return { nodes, edges };
}

export const DependencyGraphPage: React.FC = () => {
  const [deps, setDeps] = useState<Dependency[] | null>(null);
  useEffect(() => { DependencyApi.list().then(setDeps); }, []);

  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  useEffect(() => {
    if (deps) {
      const next = layout(deps);
      setNodes(next.nodes);
      setEdges(next.edges);
    }
  }, [deps, setNodes, setEdges]);

  if (deps === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Dependency Graph"
        subtitle="Conversion-order prerequisites between Oracle Fusion business objects"
      />
      <Card>
        <CardHeader
          title="Object Dependencies"
          subtitle={`${deps.length} relationship(s) — load upstream objects first to avoid downstream failures`}
        />
        <div className="h-[560px] w-full">
          {deps.length === 0 ? (
            <div className="p-5">
              <EmptyState
                icon={<Network className="h-5 w-5" />}
                title="No dependencies seeded"
                description="Seed dependencies to visualize the conversion order."
              />
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              fitView
              fitViewOptions={{ padding: 0.2 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.4}
              maxZoom={1.5}
            >
              <Background color="#E2E8F0" gap={16} />
              <Controls className="!shadow-card" />
            </ReactFlow>
          )}
        </div>
      </Card>
    </>
  );
};
