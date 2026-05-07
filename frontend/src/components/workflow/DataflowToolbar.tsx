import React from "react";
import { useReactFlow } from "reactflow";
import { Minus, Plus, AlignVerticalJustifyCenter, AlignHorizontalJustifyCenter } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  showLabels: boolean;
  setShowLabels: (b: boolean) => void;
  layout: "horizontal" | "vertical";
  setLayout: (l: "horizontal" | "vertical") => void;
  onAutoLayout: () => void;
}

export const DataflowToolbar: React.FC<Props> = ({
  showLabels, setShowLabels, layout, setLayout, onAutoLayout,
}) => {
  const { zoomTo, getZoom, zoomIn, zoomOut } = useReactFlow();
  const [zoom, setZoom] = React.useState(100);

  // Keep the % readout in sync
  React.useEffect(() => {
    const id = setInterval(() => {
      const z = Math.round(getZoom() * 100);
      setZoom((prev) => prev === z ? prev : z);
    }, 200);
    return () => clearInterval(id);
  }, [getZoom]);

  return (
    <div className="flex h-11 items-center gap-3 border-b border-line bg-white px-4">
      {/* Show labels */}
      <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-ink">
        <input
          type="checkbox"
          checked={showLabels}
          onChange={(e) => setShowLabels(e.target.checked)}
          className="h-3.5 w-3.5 rounded border-line text-brand focus:ring-brand"
        />
        Show labels
      </label>

      <div className="h-5 w-px bg-line" />

      {/* Layout */}
      <span className="text-xs text-ink-muted">Layout:</span>
      <div className="flex items-center rounded-md border border-line bg-white p-0.5">
        <button
          onClick={() => { setLayout("horizontal"); onAutoLayout(); }}
          className={cn("inline-flex items-center gap-1 rounded p-1.5", layout === "horizontal" ? "bg-canvas text-ink" : "text-ink-subtle hover:text-ink")}
          title="Left → Right"
        >
          <AlignHorizontalJustifyCenter className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => { setLayout("vertical"); onAutoLayout(); }}
          className={cn("inline-flex items-center gap-1 rounded p-1.5", layout === "vertical" ? "bg-canvas text-ink" : "text-ink-subtle hover:text-ink")}
          title="Top → Bottom"
        >
          <AlignVerticalJustifyCenter className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex-1" />

      {/* Zoom */}
      <div className="flex items-center gap-1">
        <button onClick={() => zoomOut()} className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink" title="Zoom out">
          <Minus className="h-3.5 w-3.5" />
        </button>
        <input
          type="range"
          min={25} max={200} step={5}
          value={zoom}
          onChange={(e) => zoomTo(Number(e.target.value) / 100)}
          className="w-24 accent-brand"
        />
        <button onClick={() => zoomIn()} className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink" title="Zoom in">
          <Plus className="h-3.5 w-3.5" />
        </button>
        <div className="ml-2 w-12 rounded border border-line px-2 py-1 text-center font-mono text-[11px] text-ink-muted tabular-nums">
          {zoom}%
        </div>
      </div>
    </div>
  );
};
