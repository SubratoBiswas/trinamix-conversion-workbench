import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ShieldCheck, ListChecks, RefreshCw, AlertTriangle, AlertCircle, Info } from "lucide-react";
import { ConversionsApi, QualityApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { severityTone } from "@/lib/utils";
import type {
  Conversion,
  ValidationIssue,
} from "@/types";

const SEV_ICON: Record<string, React.ElementType> = {
  critical: AlertCircle, error: AlertCircle, warning: AlertTriangle, info: Info,
};

interface PageProps {
  category: "cleansing" | "validation";
}

const IssueDashboard: React.FC<PageProps> = ({ category }) => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");
  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<number | null>(projParam ? Number(projParam) : null);
  const [items, setItems] = useState<ValidationIssue[] | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    ConversionsApi.list().then((ps) => {
      setProjects(ps);
      if (!pid && ps[0]) {
        setPid(ps[0].id);
        setParams({ conversion: String(ps[0].id) });
      }
    });
  }, []);

  const load = () => {
    if (!pid) return;
    setItems(null);
    const fn = category === "cleansing" ? QualityApi.cleansing : QualityApi.validation;
    fn(pid).then(setItems);
  };
  useEffect(load, [pid, category]);

  const run = async () => {
    if (!pid) return;
    setRunning(true);
    try {
      const fn = category === "cleansing" ? QualityApi.runCleansing : QualityApi.runValidation;
      const res = await fn(pid);
      setItems(res);
    } finally { setRunning(false); }
  };

  const summary = useMemo(() => {
    if (!items) return { critical: 0, error: 0, warning: 0, info: 0 };
    const s = { critical: 0, error: 0, warning: 0, info: 0 } as Record<string, number>;
    for (const i of items) s[i.severity] = (s[i.severity] || 0) + 1;
    return s;
  }, [items]);

  const titles = category === "cleansing"
    ? { title: "Data Cleansing", subtitle: "Quality issues detected on the source dataset", btn: "Run Cleansing" }
    : { title: "Validation", subtitle: "FBDI compliance checks on the converted output", btn: "Run Validation" };

  return (
    <>
      <PageTitle
        title={titles.title}
        subtitle={titles.subtitle}
        right={<Button onClick={run} loading={running} disabled={!pid}>
          {category === "cleansing" ? <ShieldCheck className="h-4 w-4" /> : <ListChecks className="h-4 w-4" />}
          {titles.btn}
        </Button>}
      />

      <Card className="mb-4">
        <CardBody className="!py-3">
          <div className="flex items-center gap-3">
            <label className="label !mb-0">Project</label>
            <select className="input !w-auto min-w-[280px]" value={pid ?? ""}
              onChange={(e) => { const v = Number(e.target.value); setPid(v); setParams({ conversion: String(v) }); }}>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <Button variant="secondary" onClick={load}><RefreshCw className="h-3.5 w-3.5" /></Button>
          </div>
        </CardBody>
      </Card>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <SummaryCard label="Critical / Error" value={summary.critical + summary.error} tone="danger" />
        <SummaryCard label="Warnings" value={summary.warning} tone="warning" />
        <SummaryCard label="Info" value={summary.info} tone="info" />
        <SummaryCard label="Total" value={(items?.length) || 0} tone="neutral" />
      </div>

      <Card>
        <CardHeader title="Issues" subtitle={`${items?.length ?? 0} issue(s)`} />
        {items === null ? <PageLoader /> :
          items.length === 0 ? <CardBody><EmptyState
            title={`No ${category} issues yet`}
            description={`Click "${titles.btn}" to run the engine.`}
            action={<Button onClick={run} loading={running}>{titles.btn}</Button>}
          /></CardBody> : (
          <table className="table-shell">
            <thead>
              <tr>
                <th>Severity</th><th>Type</th><th>Field</th>
                {category === "validation" && <th>Row</th>}
                <th>Message</th><th>Suggested Fix</th>
                <th>Auto?</th><th className="text-right">Impact</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => {
                const Icon = SEV_ICON[i.severity] || Info;
                return (
                  <tr key={i.id}>
                    <td>
                      <Pill tone={severityTone(i.severity)}>
                        <Icon className="h-3 w-3" /> {i.severity}
                      </Pill>
                    </td>
                    <td className="font-medium">{i.issue_type}</td>
                    <td className="text-ink-muted">{i.field_name || "—"}</td>
                    {category === "validation" && <td className="text-ink-muted">{i.row_number ?? "—"}</td>}
                    <td className="max-w-[420px] truncate" title={i.message}>{i.message}</td>
                    <td className="max-w-[300px] truncate text-ink-muted">{i.suggested_fix || "—"}</td>
                    <td>{i.auto_fixable ? <Pill tone="success">yes</Pill> : <span className="text-ink-subtle">—</span>}</td>
                    <td className="text-right tabular-nums text-ink-muted">{i.impacted_count}</td>
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

const SummaryCard: React.FC<{ label: string; value: number; tone: "danger" | "warning" | "info" | "neutral" }> = ({ label, value, tone }) => {
  const text = {
    danger: "text-danger", warning: "text-warning", info: "text-info", neutral: "text-ink",
  }[tone];
  return (
    <div className="card p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${text}`}>{value}</div>
    </div>
  );
};

export const CleansingPage: React.FC = () => <IssueDashboard category="cleansing" />;
export const ValidationPage: React.FC = () => <IssueDashboard category="validation" />;
