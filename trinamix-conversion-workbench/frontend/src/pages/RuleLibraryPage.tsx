import React, { useEffect, useState } from "react";
import { Library, Plus, Wand2, Trash2, Sparkles, GitMerge } from "lucide-react";
import { LearningApi, MappingApi } from "@/api";
import {
  Card, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
  Button,
} from "@/components/ui/Primitives";
import { formatDate } from "@/lib/utils";
import type { LearnedMapping, TransformationRule } from "@/types";

/**
 * Rule Library — combines learned rules (from `Approve & Learn` actions) with
 * project-level transformation rules. The Rule Library is the *re-usable*
 * superset; Transformation Studio is per-project.
 */
export const RuleLibraryPage: React.FC = () => {
  const [items, setItems] = useState<LearnedMapping[] | null>(null);

  useEffect(() => {
    LearningApi.list({ kind: "rule" })
      .then((rs) => setItems(rs))
      .catch(() => setItems([]));
  }, []);

  if (items === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Rule Library"
        subtitle="Reusable transformation rules captured from human approvals"
      />

      <Card>
        <CardHeader title="Rules" subtitle={`${items.length} captured rule(s)`} />
        {items.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={<Library className="h-5 w-5" />}
              title="No reusable rules captured yet"
              description="Approve a transformation recommendation in the Dataset Preparation or Mapping Review screen with the 'Apply & Learn' action — it lands here as a reusable rule."
            />
          </div>
        ) : (
          <table className="table-shell">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Type</th>
                <th>Original</th>
                <th>Resolved</th>
                <th>Object</th>
                <th>Captured</th>
                <th></th>
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
                    <button onClick={async () => { await LearningApi.delete(m.id); LearningApi.list({ kind: "rule" }).then(setItems); }}
                      className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
};
