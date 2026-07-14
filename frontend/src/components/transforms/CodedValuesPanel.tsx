import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, HelpCircle,
  ListChecks, RefreshCw, ShieldQuestion,
} from "lucide-react";
import { MappingApi, type CodedValueAudit, type CodedValueColumn } from "@/api";
import { Button, Card, CardBody, CardHeader, Pill, Spinner } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

/**
 * Coded (LOV) column audit — shown BEFORE generation.
 *
 * Oracle rejects an FBDI file whose coded column holds a value outside its
 * accepted list, and the failure comes back hours later as an opaque load error.
 * This panel says up front exactly what the tool will write into every coded
 * column, how it decided, and which columns it refuses to guess at.
 */

const STATUS: Record<
  CodedValueColumn["status"],
  { tone: "success" | "warning" | "danger" | "info"; label: string; icon: React.ElementType }
> = {
  ok: { tone: "success", label: "Ready", icon: CheckCircle2 },
  confirm: { tone: "warning", label: "Confirm", icon: HelpCircle },
  error: { tone: "danger", label: "Will fail load", icon: AlertTriangle },
  unverified: { tone: "info", label: "Needs your lookup codes", icon: ShieldQuestion },
};

const CodedRow: React.FC<{ col: CodedValueColumn }> = ({ col }) => {
  const [open, setOpen] = useState(col.status === "error");
  const meta = STATUS[col.status];
  const Icon = meta.icon;

  return (
    <div className="border-b border-line last:border-0">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-canvas"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-ink-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-ink-muted" />
        )}
        <Icon
          className={cn(
            "h-4 w-4 shrink-0",
            col.status === "ok" && "text-success",
            col.status === "confirm" && "text-warning",
            col.status === "error" && "text-danger",
            col.status === "unverified" && "text-info",
          )}
        />
        <span className="flex-1 truncate">
          <span className="font-mono text-xs text-ink">{col.target_field}</span>
          {col.required && (
            <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-danger">
              required
            </span>
          )}
          {col.source_column && (
            <span className="ml-2 text-xs text-ink-muted">← {col.source_column}</span>
          )}
        </span>
        {col.lookup_type && (
          <span className="hidden font-mono text-[10px] text-ink-muted md:inline">
            {col.lookup_type}
          </span>
        )}
        <Pill tone={meta.tone}>{meta.label}</Pill>
      </button>

      {open && (
        <div className="space-y-3 bg-canvas px-4 pb-4 pl-11 pt-1 text-xs">
          {col.message && <p className="text-ink-muted">{col.message}</p>}

          {col.allowed_codes.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-ink">Accepted by Oracle</div>
              <div className="flex flex-wrap gap-1.5">
                {col.allowed_codes.map(c => (
                  <span
                    key={c.code}
                    className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink"
                    title={c.meaning}
                  >
                    {c.code}
                    {c.meaning && <span className="ml-1 text-ink-muted">{c.meaning}</span>}
                  </span>
                ))}
              </div>
              {col.codes_source === "oracle_standard" && (
                <p className="mt-1 text-[11px] text-ink-muted">
                  Oracle-standard codes — confirm against your instance in Manage Standard Lookups.
                </p>
              )}
            </div>
          )}

          {col.resolved.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-ink">What will be written</div>
              <table className="w-full">
                <tbody>
                  {col.resolved.map(r => (
                    <tr key={r.from} className="align-top">
                      <td className="py-0.5 pr-2 text-ink-muted">{r.from}</td>
                      <td className="w-6 py-0.5 text-ink-muted">→</td>
                      <td className="w-16 py-0.5 pr-3 font-mono font-semibold text-ink">{r.to}</td>
                      <td className="py-0.5 text-[11px] text-ink-muted">{r.how}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {col.unresolved.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-danger">
                Not matched to any accepted code
              </div>
              <div className="flex flex-wrap gap-1.5">
                {col.unresolved.map(v => (
                  <span
                    key={v}
                    className="rounded border border-danger/30 bg-danger-subtle px-1.5 py-0.5 text-[11px] text-danger"
                  >
                    {v}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-[11px] text-ink-muted">
                {col.required
                  ? "Required column — add a value rule or fix the source before generating."
                  : "Optional column — these cells are written blank so the file still loads."}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const CodedValuesPanel: React.FC<{ conversionId: string }> = ({ conversionId }) => {
  const [data, setData] = useState<CodedValueAudit | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [onlyIssues, setOnlyIssues] = useState(true);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await MappingApi.codedValues(conversionId));
    } catch {
      setErr("Couldn't audit the coded columns. The source file may still be uploading.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversionId]);

  const s = data?.summary ?? {};
  const issues = (s.error ?? 0) + (s.confirm ?? 0) + (s.unverified ?? 0);

  const rows = useMemo(() => {
    const cols = data?.columns ?? [];
    return onlyIssues ? cols.filter(c => c.status !== "ok") : cols;
  }, [data, onlyIssues]);

  if (loading && !data) {
    return (
      <Card className="mb-4">
        <CardBody className="flex items-center gap-2 text-sm text-ink-muted">
          <Spinner /> Checking coded columns against Oracle's accepted values…
        </CardBody>
      </Card>
    );
  }

  if (err) {
    return (
      <Card className="mb-4">
        <CardBody className="flex items-center justify-between text-sm text-ink-muted">
          <span>{err}</span>
          <Button variant="ghost" onClick={() => void load()}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" /> Retry
          </Button>
        </CardBody>
      </Card>
    );
  }

  if (!data || (s.coded_columns ?? 0) === 0) return null;

  return (
    <Card className="mb-4">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-brand" />
            Coded values (Oracle LOVs)
          </span>
        }
        subtitle={
          issues === 0
            ? `All ${s.coded_columns} coded columns will be written with values Oracle accepts.`
            : `${s.coded_columns} coded columns. ${issues} need a look before you generate.`
        }
        actions={
          <div className="flex items-center gap-2">
            {(s.error ?? 0) > 0 && <Pill tone="danger">{s.error} will fail</Pill>}
            {(s.confirm ?? 0) > 0 && <Pill tone="warning">{s.confirm} confirm</Pill>}
            {(s.unverified ?? 0) > 0 && <Pill tone="info">{s.unverified} lookup</Pill>}
            {(s.ok ?? 0) > 0 && <Pill tone="success">{s.ok} ready</Pill>}
            <Button variant="ghost" onClick={() => setExpanded(e => !e)}>
              {expanded ? "Hide" : "Review"}
            </Button>
          </div>
        }
      />

      {expanded && (
        <>
          <div className="flex items-center justify-between border-b border-line px-4 py-2 text-xs">
            <label className="flex cursor-pointer items-center gap-2 text-ink-muted">
              <input
                type="checkbox"
                checked={onlyIssues}
                onChange={e => setOnlyIssues(e.target.checked)}
                className="h-3.5 w-3.5"
              />
              Only show columns that need attention
            </label>
            <Button variant="ghost" onClick={() => void load()} loading={loading}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> Re-check
            </Button>
          </div>
          <div>
            {rows.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-ink-muted">
                Nothing needs attention — every coded column resolves to an accepted Oracle value.
              </p>
            ) : (
              rows.map(c => <CodedRow key={c.target_field} col={c} />)
            )}
          </div>
        </>
      )}
    </Card>
  );
};

export default CodedValuesPanel;
