import { Edge, Node } from "reactflow";

/**
 * Layered auto-layout for dataflow canvases.
 *
 * Computes each node's depth as the longest path from any root (a node with no
 * incoming edges), then positions columns L→R or rows T→B based on `direction`.
 * Cycles are tolerated — they'll fall back to depth 0.
 */
export function autoLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "horizontal" | "vertical" = "horizontal",
): Node[] {
  if (nodes.length === 0) return nodes;

  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const n of nodes) { incoming.set(n.id, []); outgoing.set(n.id, []); }
  for (const e of edges) {
    incoming.get(e.target)?.push(e.source);
    outgoing.get(e.source)?.push(e.target);
  }

  // Compute depth via BFS from roots
  const depth = new Map<string, number>();
  const roots = nodes.filter(n => (incoming.get(n.id)?.length || 0) === 0);
  const q: [string, number][] = roots.map(r => [r.id, 0]);
  if (q.length === 0 && nodes[0]) q.push([nodes[0].id, 0]);
  const visited = new Set<string>();
  while (q.length) {
    const [id, d] = q.shift()!;
    if ((depth.get(id) ?? -1) >= d) continue;
    depth.set(id, d);
    if (visited.has(id) && d > 50) continue; // safety for cycles
    visited.add(id);
    for (const next of outgoing.get(id) || []) q.push([next, d + 1]);
  }

  // Group by depth
  const byDepth = new Map<number, string[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    byDepth.set(d, [...(byDepth.get(d) || []), n.id]);
  }

  // Lay out
  const COL_W = 220, ROW_H = 140, OFFSET_X = 60, OFFSET_Y = 40;
  const positionById = new Map<string, { x: number; y: number }>();
  Array.from(byDepth.entries())
    .sort((a, b) => a[0] - b[0])
    .forEach(([d, ids]) => {
      ids.forEach((id, i) => {
        const x = direction === "horizontal" ? d * COL_W + OFFSET_X : i * COL_W + OFFSET_X;
        const y = direction === "horizontal" ? i * ROW_H + OFFSET_Y : d * ROW_H + OFFSET_Y;
        positionById.set(id, { x, y });
      });
    });

  return nodes.map(n => ({
    ...n,
    position: positionById.get(n.id) || n.position,
  }));
}
