import React, { useEffect, useState } from "react";
import { Library, Plus, Pencil, Trash2 } from "lucide-react";
import { LearningApi } from "@/api";
import {
  Card, CardHeader, EmptyState, PageLoader, PageTitle, Pill, Button,
} from "@/components/ui/Primitives";
import { LearnedEntryModal } from "@/components/LearnedEntryModal";
import { formatDate } from "@/lib/utils";
import type { LearnedMapping } from "@/types";

/**
 * Rule Library — reusable transformation rules. Captured automatically from
 * "Apply & Learn" approvals, and editable/creatable manually here.
 */
export const RuleLibraryPage: React.FC = () => {
  const [items, setItems] = useState<LearnedMapping[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LearnedMapping | null>(null);

  const refresh = () =>
    LearningApi.list({ kind: "rule" }).then(setItems).catch(() => setItems([]));

  useEffect(() => { refresh(); }, []);

  const openAdd = () => { setEditing(null); setModalOpen(true); };
  const openEdit = (m: LearnedMapping) => { setEditing(m); setModalOpen(true); };
  const remove = async (m: LearnedMapping) => {
    if (!window.confirm(`Delete rule "${m.category}"?`)) return;
    await LearningApi.delete(m.id);
    refresh();
  };

  if (items === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Rule Library"
        subtitle="Reusable transformation rules captured from approvals — or added manually"
        right={<Button onClick={openAdd}><Plus className="h-4 w-4" /> Add rule</Button>}
      />

      <Card>
        <CardHeader title="Rules" subtitle={`${items.length} rule(s)`} />
        {items.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={<Library className="h-5 w-5" />}
              title="No rules yet"
              description="Approve a transformation with 'Apply & Learn', or add one manually."
              action={<Button onClick={openAdd}><Plus className="h-4 w-4" /> Add rule</Button>}
            />
          </div>
        ) : (
          <table className="table-shell">
            <thead>
              <tr>
                <th>Rule</th><th>Type</th><th>Original</th><th>Resolved</th>
                <th>Object</th><th>Captured</th><th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id}>
                  <td className="font-medium">{m.category}</td>
                  <td>{m.rule_type ? <Pill tone="brand">{m.rule_type}</Pill> : "—"}</td>
                  <td className="font-mono text-danger">{m.original_value}</td>
                  <td className="font-mono text-success">{m.resolved_value}</td>
                  <td className="text-ink-muted">{m.target_object || "—"}</td>
                  <td className="text-[11px] text-ink-muted">{formatDate(m.captured_at)}</td>
                  <td className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(m)} title="Edit rule"
                        className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-brand">
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button onClick={() => remove(m)} title="Delete rule"
                        className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <LearnedEntryModal
        open={modalOpen} kind="rule" initial={editing}
        onClose={() => setModalOpen(false)} onSaved={refresh}
      />
    </>
  );
};
