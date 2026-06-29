import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Plus, Pencil, Trash2 } from "lucide-react";
import { LearningApi } from "@/api";
import {
  Card, CardHeader, EmptyState, PageLoader, PageTitle, Pill, Button,
} from "@/components/ui/Primitives";
import { LearnedEntryModal } from "@/components/LearnedEntryModal";
import { formatDate } from "@/lib/utils";
import type { LearnedMapping } from "@/types";

/**
 * Crosswalks — value-translation tables. Captured automatically when an analyst
 * approves a value-mapping (e.g. status A -> Active), and editable/creatable here.
 */
export const CrosswalkLibraryPage: React.FC = () => {
  const [items, setItems] = useState<LearnedMapping[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LearnedMapping | null>(null);

  const refresh = () =>
    LearningApi.list({ kind: "crosswalk" }).then(setItems).catch(() => setItems([]));

  useEffect(() => { refresh(); }, []);

  const openAdd = () => { setEditing(null); setModalOpen(true); };
  const openEdit = (m: LearnedMapping) => { setEditing(m); setModalOpen(true); };
  const remove = async (m: LearnedMapping) => {
    if (!window.confirm(`Delete crosswalk "${m.original_value} -> ${m.resolved_value}"?`)) return;
    await LearningApi.delete(m.id);
    refresh();
  };

  const byCategory = useMemo(() => {
    const out: Record<string, LearnedMapping[]> = {};
    for (const i of items || []) out[i.category] = [...(out[i.category] || []), i];
    return out;
  }, [items]);

  // Reach: how many distinct object/field placements share the same value
  // translation (legacy -> Fusion) — i.e. how widely a crosswalk is reused.
  const usage = useMemo(() => {
    const m: Record<string, Set<string>> = {};
    for (const i of items || []) {
      const key = `${i.original_value}→${i.resolved_value}`;
      (m[key] = m[key] || new Set<string>()).add(`${i.target_object || "-"}::${i.target_field || "-"}`);
    }
    return m;
  }, [items]);
  const usedBy = (i: LearnedMapping) => usage[`${i.original_value}→${i.resolved_value}`]?.size || 1;
  const coverage = (rows: LearnedMapping[]) => {
    const o = new Set<string>(), f = new Set<string>();
    for (const r of rows) {
      if (r.target_object) o.add(r.target_object.toLowerCase());
      if (r.target_field) f.add(r.target_field.toLowerCase());
    }
    return { objects: o.size, fields: f.size };
  };
  const totals = coverage(items || []);

  if (items === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Crosswalk Library"
        subtitle="Value-translation tables maintained from approvals — or added manually"
        right={<Button onClick={openAdd}><Plus className="h-4 w-4" /> Add crosswalk</Button>}
      />

      {items.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="rounded-full bg-brand-subtle px-2.5 py-1 font-medium text-brand-dark">
            {items.length} value mapping{items.length === 1 ? "" : "s"}
          </span>
          <span className="rounded-full border border-line bg-white px-2.5 py-1 text-ink-muted">
            used across {totals.objects} object{totals.objects === 1 ? "" : "s"}
          </span>
          <span className="rounded-full border border-line bg-white px-2.5 py-1 text-ink-muted">
            {totals.fields} field{totals.fields === 1 ? "" : "s"}
          </span>
        </div>
      )}

      {items.length === 0 ? (
        <Card>
          <div className="p-6">
            <EmptyState
              icon={<ArrowLeftRight className="h-5 w-5" />}
              title="No crosswalks captured yet"
              description="Crosswalks are stored automatically when an analyst approves a value-mapping (e.g. A -> Active). None have been approved yet — add one manually to get started."
              action={<Button onClick={openAdd}><Plus className="h-4 w-4" /> Add crosswalk</Button>}
            />
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {Object.entries(byCategory).map(([cat, rows]) => (
            <Card key={cat}>
              <CardHeader
                title={cat}
                subtitle={(() => { const c = coverage(rows); return `${rows.length} value mapping(s) · ${c.objects} object(s) · ${c.fields} field(s)`; })()}
                actions={<Pill tone="brand">crosswalk</Pill>}
              />
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>Original (legacy)</th><th>Resolved (Fusion)</th>
                    <th>Target object</th><th>Used by</th><th>Captured</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((m) => (
                    <tr key={m.id}>
                      <td className="font-mono text-danger">{m.original_value}</td>
                      <td className="font-mono text-success">{m.resolved_value}</td>
                      <td className="text-ink-muted">{m.target_object || "—"}</td>
                      <td>{(() => { const n = usedBy(m); return <Pill tone={n > 1 ? "brand" : "neutral"}>{n} {n === 1 ? "field" : "fields"}</Pill>; })()}</td>
                      <td className="text-[11px] text-ink-muted">{formatDate(m.captured_at)}</td>
                      <td className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => openEdit(m)} title="Edit crosswalk"
                            className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-brand">
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button onClick={() => remove(m)} title="Delete crosswalk"
                            className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      )}

      <LearnedEntryModal
        open={modalOpen} kind="crosswalk" initial={editing}
        onClose={() => setModalOpen(false)} onSaved={refresh}
      />
    </>
  );
};
