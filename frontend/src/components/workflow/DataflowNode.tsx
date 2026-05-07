import React from "react";
import { Handle, Position, NodeProps } from "reactflow";
import { CheckCircle2, AlertCircle, Loader2, GitBranch } from "lucide-react";
import { NODE_TYPES } from "./NodeRegistry";
import { cn } from "@/lib/utils";

interface DataflowNodeData {
  nodeType: string;
  label?: string;
  runStatus?: string;
  detail?: string;
}

/**
 * Custom React-Flow node rendered to look like Oracle Analytics Cloud's
 * dataflow nodes: a small rounded card with a coloured square containing the
 * icon, the type label below, and an optional run-status badge.
 *
 * Each node has both source and target handles so connections can flow either
 * way; the runner enforces topological order.
 */
export const DataflowNode: React.FC<NodeProps<DataflowNodeData>> = ({ data, selected }) => {
  const def = NODE_TYPES[data.nodeType];
  const Icon = def?.icon || GitBranch;
  const status = data.runStatus;

  return (
    <div className={cn(
      "relative flex w-[150px] flex-col items-center rounded-md border-2 bg-white px-2 py-3 transition",
      selected ? "border-brand shadow-soft" : "border-line hover:border-ink-subtle"
    )}>
      <Handle type="target" position={Position.Left}  style={handleStyle} />
      <Handle type="source" position={Position.Right} style={handleStyle} />

      {/* Coloured icon square — the visual anchor */}
      <div className={cn(
        "flex h-12 w-12 items-center justify-center rounded-md border",
        def?.bg, def?.accent
      )}>
        <Icon className="h-5 w-5" />
      </div>

      {/* Label */}
      <div className="mt-2 text-center text-[11.5px] font-semibold leading-tight text-ink line-clamp-2">
        {data.label || def?.label || data.nodeType}
      </div>

      {/* Subtitle (config summary) */}
      {data.detail && (
        <div className="mt-0.5 max-w-full truncate text-center text-[10px] text-ink-muted" title={data.detail}>
          {data.detail}
        </div>
      )}

      {/* Run status badge */}
      {status && (
        <div className={cn(
          "absolute -right-1 -top-1 flex h-4.5 w-4.5 items-center justify-center rounded-full border-2 border-white",
          status === "ok" ? "bg-success" :
          status === "error" ? "bg-danger" :
          status === "skipped" ? "bg-ink-subtle" : "bg-warning"
        )}>
          {status === "ok" ? <CheckCircle2 className="h-3 w-3 text-white" /> :
           status === "error" ? <AlertCircle className="h-3 w-3 text-white" /> :
           <Loader2 className="h-3 w-3 animate-spin text-white" />}
        </div>
      )}
    </div>
  );
};

const handleStyle = {
  background: "#94A3B8",
  width: 7,
  height: 7,
  border: "1.5px solid white",
};
