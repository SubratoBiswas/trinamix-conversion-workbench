import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Plus, Trash2, Sparkles } from "lucide-react";
import { LearningApi } from "@/api";
import {
  Card, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type { LearnedMapping } from "@/types";

/**
 * Crosswalks — value-translation tables. Examples: status A/I → Active/Inactive,
 * UOM EA → Each, country US → United States.
 */
export const CrosswalkLibraryPage: React.FC = () => {
  const [items, setItems] = useState<LearnedMapping[] | null>(null);

  useEffect(() => {
    LearningApi.list({ kind: "crosswalk" })
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  // Group by category — UOM, Status, Currency, etc.
  const byCategory = useMemo(() => {
    if (!items) return {};
    const out: Record<string, LearnedMapping[]> = {};
    for (const i of items) {
      out[i.category] = [...(out[i.category] || []), i];
    }
    return out;
  }, [items]);

  if (items === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Crosswalk Library"
        subtitle="Value-translation tables maintained from approval history"
      />

      {items.length === 0 ? (
        <Card>
          <div className="p-6">
            <EmptyState
              icon={<ArrowLeftRight className="h-5 w-5" />}
              title="No crosswalks captured yet"
              description="When an analyst approves a value-mapping recommendation (e.g. A → Active), the resolved values are stored here as a reusable crosswalk."
            />
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {Object.entries(byCategory).map(([cat, rows]) => (
            <Card key={cat}>
              <CardHeader
                title={cat}
                subtitle={`${rows.length} value mapping(s)`}
                actions={<Pill tone="brand">crosswalk</Pill>}
              />
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>Original (legacy)</th>
                    <th>Resolved (Fusion)</th>
                    <th>Target object</th>
                    <th>Captured</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((m) => (
                    <tr key={m.id}>
                      <td className="font-mono text-danger">{m.original_value}</td>
                      <td className="font-mono text-success">{m.resolved_value}</td>
                      <td className="text-ink-muted">{m.target_object || "—"}</td>
                      <td className="text-[11px] text-ink-muted">{formatDate(m.captured_at)}</td>
                      <td className="text-right">
                        <button onClick={async () => {
                          await LearningApi.delete(m.id);
                          LearningApi.list({ kind: "crosswalk" }).then(setItems);
                        }} className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      )}
    </>
  );
};
