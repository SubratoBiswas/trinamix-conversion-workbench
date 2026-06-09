import React, { useEffect, useMemo, useState } from "react";
import { CheckCircle2, FileEdit, Sparkles, ShieldCheck } from "lucide-react";
import { ConversionsApi, ProjectsApi, MappingApi, LoadApi } from "@/api";
import {
  Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate, statusTone } from "@/lib/utils";
import type { Conversion, MappingSuggestion, LoadRun } from "@/types";

interface AuditEntry {
  ts: string;
  actor: string;
  project: string;
  action: string;
  detail: string;
  tone: "info" | "success" | "warning" | "danger" | "neutral";
  icon: React.ElementType;
}

export const AuditPage: React.FC = () => {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);

  useEffect(() => {
    (async () => {
      // Audit walks Conversions, not engagement-level Projects, because that's
      // where the operational events happen.
      const conversions: Conversion[] = await ConversionsApi.list();
      const all: AuditEntry[] = [];

      for (const c of conversions) {
        all.push({
          ts: c.created_at, actor: c.created_by, project: c.name,
          action: "Conversion created",
          detail: c.dataset_id && c.template_id
            ? `Bound to dataset #${c.dataset_id} → template #${c.template_id}`
            : "Planned (no source / target yet)",
          tone: "info", icon: FileEdit,
        });
        if (["loaded", "validated", "output_generated"].includes(c.status)) {
          all.push({
            ts: c.updated_at, actor: c.created_by, project: c.name,
            action: `Status → ${c.status.replaceAll("_", " ")}`,
            detail: "Conversion advanced through the pipeline.",
            tone: statusTone(c.status) === "success" ? "success" : "info", icon: ShieldCheck,
          });
        }

        if (!c.dataset_id || !c.template_id) continue;  // can't list mappings on planning rows

        // Mapping approvals
        try {
          const ms: MappingSuggestion[] = await MappingApi.list(c.id);
          for (const m of ms) {
            if (m.status === "approved" && m.approved_at) {
              all.push({
                ts: m.approved_at, actor: m.approved_by || "—", project: c.name,
                action: "Mapping approved",
                detail: `${m.source_column || "(default)"} → ${m.target_field_name} (confidence ${Math.round(m.confidence * 100)}%)`,
                tone: "success", icon: CheckCircle2,
              });
            } else if (m.status === "rejected") {
              all.push({
                ts: m.approved_at || c.updated_at, actor: m.approved_by || "—", project: c.name,
                action: "Mapping rejected",
                detail: `${m.source_column || "(blank)"} → ${m.target_field_name}`,
                tone: "danger", icon: FileEdit,
              });
            } else if (m.status === "overridden") {
              all.push({
                ts: m.approved_at || c.updated_at, actor: m.approved_by || "—", project: c.name,
                action: "Mapping overridden",
                detail: `${m.target_field_name} ← ${m.source_column || "(default)"}`,
                tone: "warning", icon: Sparkles,
              });
            }
          }
        } catch { /* ignore */ }

        // Load runs
        try {
          const runs: LoadRun[] = await LoadApi.runs(c.id);
          for (const r of runs) {
            all.push({
              ts: r.started_at, actor: "system", project: c.name,
              action: `Load ${r.run_type} — ${r.status}`,
              detail: `Total ${r.total_records} · Passed ${r.passed_count} · Failed ${r.failed_count}`,
              tone: r.failed_count === 0 ? "success" : "danger",
              icon: ShieldCheck,
            });
          }
        } catch { /* ignore */ }
      }
      all.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
      setEntries(all);
    })();
  }, []);

  return (
    <>
      <PageTitle
        title="Audit Trail"
        subtitle="Project lifecycle, mapping approvals, and load events"
      />
      <Card>
        <CardHeader title="Activity" subtitle={`${entries?.length ?? 0} event(s)`} />
        {entries === null ? <PageLoader /> :
          entries.length === 0 ? <CardBody><EmptyState title="No activity yet" description="Approve mappings or run a load to see events here." /></CardBody> :
            <table className="table-shell">
              <thead><tr><th>Timestamp</th><th>Actor</th><th>Project</th><th>Action</th><th>Detail</th></tr></thead>
              <tbody>
                {entries.map((e, i) => {
                  const Icon = e.icon;
                  return (
                    <tr key={i}>
                      <td className="whitespace-nowrap text-ink-muted">{formatDate(e.ts)}</td>
                      <td className="text-ink-muted">{e.actor}</td>
                      <td className="font-medium">{e.project}</td>
                      <td>
                        <span className="inline-flex items-center gap-1.5">
                          <Icon className={
                            "h-3.5 w-3.5 " +
                            (e.tone === "success" ? "text-success" :
                              e.tone === "danger" ? "text-danger" :
                                e.tone === "warning" ? "text-warning" : "text-info")
                          } />
                          <span>{e.action}</span>
                        </span>
                      </td>
                      <td className="text-ink-muted">{e.detail}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
        }
      </Card>
    </>
  );
};
