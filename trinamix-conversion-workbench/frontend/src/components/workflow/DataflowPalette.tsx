import React, { useMemo, useState } from "react";
import { Search, Database, ChevronRight } from "lucide-react";
import { NODE_CATEGORIES, NODE_TYPES, groupedNodeTypes, type NodeCategory } from "./NodeRegistry";
import { cn } from "@/lib/utils";

const CATEGORY_HEADER_TONE: Record<NodeCategory, string> = {
  data_sources:    "text-emerald-600",
  conversion:      "text-indigo-600",
  transformations: "text-violet-600",
  quality:         "text-amber-600",
  output:          "text-rose-600",
};

interface Props {
  /** Tab the user is on — "datasets" mirrors OAC's secondary tab. */
  tab?: "actions" | "datasets";
  setTab?: (t: "actions" | "datasets") => void;
}

export const DataflowPalette: React.FC<Props> = ({ tab = "actions", setTab }) => {
  const [search, setSearch] = useState("");

  const grouped = useMemo(() => groupedNodeTypes(), []);
  const term = search.toLowerCase();

  const onDragStart = (e: React.DragEvent, type: string) => {
    e.dataTransfer.setData("application/trinamix-node", type);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside className="flex h-full w-[260px] shrink-0 flex-col border-r border-line bg-white">
      {/* OAC-style icon tabs at the very top */}
      <div className="flex border-b border-line">
        <button
          onClick={() => setTab?.("datasets")}
          className={cn(
            "flex-1 border-b-2 px-3 py-2.5 text-center text-xs font-semibold transition",
            tab === "datasets"
              ? "border-brand text-brand-dark"
              : "border-transparent text-ink-muted hover:text-ink"
          )}
          title="Browse datasets"
        >
          <Database className="mx-auto mb-0.5 h-3.5 w-3.5" />
          <div className="text-[10px] uppercase tracking-wider">Sources</div>
        </button>
        <button
          onClick={() => setTab?.("actions")}
          className={cn(
            "flex-1 border-b-2 px-3 py-2.5 text-center text-xs font-semibold transition",
            tab === "actions"
              ? "border-brand text-brand-dark"
              : "border-transparent text-ink-muted hover:text-ink"
          )}
          title="Drag actions onto the canvas"
        >
          <ChevronRight className="mx-auto mb-0.5 h-3.5 w-3.5" />
          <div className="text-[10px] uppercase tracking-wider">Actions</div>
        </button>
      </div>

      <div className="border-b border-line p-2.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle" />
          <input
            className="input !pl-8 !text-xs"
            placeholder="Search actions…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {NODE_CATEGORIES.map((cat) => {
          const items = grouped[cat.key].filter((d) =>
            !term ||
            d.label.toLowerCase().includes(term) ||
            d.description.toLowerCase().includes(term)
          );
          if (items.length === 0) return null;
          return (
            <div key={cat.key} className="mb-3">
              <div className={cn(
                "px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider",
                CATEGORY_HEADER_TONE[cat.key]
              )}>
                {cat.label}
              </div>
              <div className="space-y-0.5">
                {items.map((d) => {
                  const Icon = d.icon;
                  return (
                    <div
                      key={d.type}
                      draggable
                      onDragStart={(e) => onDragStart(e, d.type)}
                      title={d.description}
                      className="group flex cursor-grab items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px] text-ink transition hover:bg-canvas active:cursor-grabbing"
                    >
                      <div className={cn(
                        "flex h-6 w-6 items-center justify-center rounded border",
                        d.bg, d.accent
                      )}>
                        <Icon className="h-3 w-3" />
                      </div>
                      <span className="flex-1 truncate">{d.label}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}

        {NODE_CATEGORIES.every((c) => grouped[c.key].filter((d) =>
          !term || d.label.toLowerCase().includes(term) || d.description.toLowerCase().includes(term)
        ).length === 0) && (
          <div className="px-3 py-6 text-center text-xs text-ink-muted">
            No actions match "{search}".
          </div>
        )}
      </div>

      {/* Footer hint */}
      <div className="border-t border-line bg-canvas px-3 py-2 text-[10.5px] text-ink-muted">
        Drag actions onto the canvas to add steps.
      </div>
    </aside>
  );
};
