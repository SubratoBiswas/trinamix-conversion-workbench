import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, X, Clock, ListChecks } from "lucide-react";
import { ConversionsApi, MappingApi } from "@/api";
import {
  Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
  Button,
} from "@/components/ui/Primitives";
import { confidenceTone, formatDate, statusTone } from "@/lib/utils";
import type {
  Conversion,
  MappingSuggestion,
} from "@/types";

interface PendingItem {
  project: Conversion;
  mapping: MappingSuggestion;
}

/**
 * Governance approval queue — surfaces medium- and low-confidence mappings
 * that are still in `suggested` status across all projects. Approvals here
 * route to the same backend endpoints used by Mapping Review.
 */
export const ApprovalsPage: React.FC = () => {
  const [pending, setPending] = useState<PendingItem[] | null>(null);

  const refresh = async () => {
    const projects = await ConversionsApi.list();
    const all: PendingItem[] = [];
    for (const p of projects) {
      try {
        const ms = await MappingApi.list(p.id);
        for (const m of ms) {
          if (m.status === "suggested" && m.source_column) {
            all.push({ project: p, mapping: m });
          }
        }
      } catch { /* ignore */ }
    }
    setPending(all.sort((a, b) => a.mapping.confidence - b.mapping.confidence));
  };
  useEffect(() => { refresh(); }, []);

  if (pending === null) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Approvals"
        subtitle="Pending AI mapping suggestions awaiting human review"
      />

      <Card>
        <CardHeader title="Approval Queue"
          subtitle={`${pending.length} suggestion(s) awaiting review`}
          actions={pending.length > 0 ? <Pill tone="warning">{pending.filter(p => p.mapping.target_required).length} required</Pill> : null}
        />
        {pending.length === 0 ? (
          <CardBody>
            <EmptyState
              icon={<ListChecks className="h-5 w-5" />}
              title="No pending approvals"
              description="All AI mapping suggestions have been actioned, or no projects have run mapping yet."
            />
          </CardBody>
        ) : (
          <table className="table-shell">
            <thead>
              <tr>
                <th>Project</th>
                <th>Target field</th>
                <th>Source column</th>
                <th>Confidence</th>
                <th>AI reason</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pending.map(({ project, mapping }) => {
                const tone = confidenceTone(mapping.confidence);
                const conf = Math.round(mapping.confidence * 100);
                return (
                  <tr key={`${project.id}-${mapping.id}`}>
                    <td>
                      <Link to={`/projects/${project.id}`} className="font-medium text-ink hover:text-brand-dark">
                        {project.name}
                      </Link>
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium">{mapping.target_field_name}</span>
                        {mapping.target_required && <Pill tone="danger">REQ</Pill>}
                      </div>
                    </td>
                    <td><code className="rounded bg-canvas px-1.5 py-0.5 text-[12px]">{mapping.source_column}</code></td>
                    <td>
                      <span className={
                        tone === "success" ? "font-mono text-success" :
                        tone === "warning" ? "font-mono text-warning" : "font-mono text-danger"
                      }>{conf}%</span>
                    </td>
                    <td className="max-w-[320px] truncate text-xs text-ink-muted" title={mapping.reason || ""}>
                      {mapping.reason || "—"}
                    </td>
                    <td className="text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          onClick={async () => { await MappingApi.update(mapping.id, { status: "rejected" }); refresh(); }}
                          className="btn-ghost h-7 px-2 text-xs text-danger hover:bg-danger-subtle"
                        >
                          <X className="h-3 w-3" /> Reject
                        </button>
                        <button
                          onClick={async () => { await MappingApi.approve(mapping.id); refresh(); }}
                          className="btn-ghost h-7 px-2 text-xs text-success hover:bg-success-subtle"
                        >
                          <Check className="h-3 w-3" /> Approve
                        </button>
                        <Link
                          to={`/mappings?project=${project.id}`}
                          className="btn-ghost h-7 px-2 text-xs"
                        >
                          Review →
                        </Link>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
};
