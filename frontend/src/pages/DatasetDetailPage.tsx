import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Hash, Calendar, Type, ToggleLeft, AlignLeft, Lock, ShieldOff } from "lucide-react";
import { DatasetsApi } from "@/api";
import { Card, CardHeader, PageLoader, PageTitle, Pill, Tabs } from "@/components/ui/Primitives";
import type { DatasetDetail, DatasetPreview } from "@/types";

const PII_CATEGORIES: { value: string; label: string }[] = [
  { value: "PII",  label: "PII (name, email, SSN)" },
  { value: "PHI",  label: "PHI (health)" },
  { value: "PCI",  label: "PCI (card / bank)" },
  { value: "FIN",  label: "FIN (financials)" },
  { value: "GOVT", label: "GOVT (tax / identifier)" },
];

const TYPE_ICON: Record<string, React.ElementType> = {
  string: AlignLeft, integer: Hash, float: Hash, date: Calendar, boolean: ToggleLeft,
};

export const DatasetDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<DatasetDetail | null>(null);
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [tab, setTab] = useState("profile");

  useEffect(() => {
    if (!id) return;
    DatasetsApi.get(Number(id)).then(setData);
    DatasetsApi.preview(Number(id), 30).then(setPreview);
  }, [id]);

  if (!data) return <PageLoader />;

  return (
    <>
      <Link to="/datasets" className="mb-3 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3 w-3" /> Back to Datasets
      </Link>
      <PageTitle
        title={data.name}
        subtitle={`${data.file_name} · ${data.row_count} rows × ${data.column_count} columns`}
        right={<Pill tone="success">{data.status}</Pill>}
      />

      <Card>
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "profile", label: "Column Profile", count: data.columns.length },
            { value: "preview", label: "Data Preview", count: preview?.total_rows },
          ]}
        />
        {tab === "profile" && (
          <table className="table-shell">
            <thead>
              <tr>
                <th>#</th><th>Column</th><th>Inferred Type</th>
                <th className="text-right">Null %</th>
                <th className="text-right">Distinct</th>
                <th>Sample Values</th>
                <th>Pattern</th>
                <th>Sensitivity</th>
              </tr>
            </thead>
            <tbody>
              {data.columns.map((c) => {
                const Icon = c.inferred_type ? TYPE_ICON[c.inferred_type] || Type : Type;
                const isPII = !!c.contains_pii;
                const onTogglePII = async () => {
                  const next = !isPII;
                  let category = c.pii_category || null;
                  if (next && !category) category = "PII";
                  const fresh = await DatasetsApi.setColumnPII(c.id, {
                    contains_pii: next,
                    pii_category: category,
                  });
                  setData(fresh);
                };
                const onChangeCategory = async (value: string) => {
                  const fresh = await DatasetsApi.setColumnPII(c.id, {
                    contains_pii: true, pii_category: value,
                  });
                  setData(fresh);
                };
                return (
                  <tr key={c.id}>
                    <td className="text-ink-muted">{c.position + 1}</td>
                    <td className="font-medium text-ink">
                      <span className="inline-flex items-center gap-1.5">
                        {c.column_name}
                        {isPII && (
                          <span
                            className="inline-flex items-center gap-1 rounded-full bg-danger/10 px-1.5 py-0.5 text-[9.5px] font-semibold text-danger"
                            title={`Sensitive · ${c.pii_category || "PII"} — must be pseudonymised before load`}
                          >
                            <Lock className="h-2.5 w-2.5" /> {c.pii_category || "PII"}
                          </span>
                        )}
                      </span>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1 text-ink-muted">
                        <Icon className="h-3.5 w-3.5" /> {c.inferred_type || "—"}
                      </span>
                    </td>
                    <td className="text-right tabular-nums text-ink-muted">{c.null_percent}%</td>
                    <td className="text-right tabular-nums text-ink-muted">{c.distinct_count}</td>
                    <td className="max-w-[320px] truncate text-ink-muted">
                      {(c.sample_values || []).slice(0, 5).join(", ") || "—"}
                    </td>
                    <td className="text-ink-muted">{c.pattern_summary || "—"}</td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={onTogglePII}
                          className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium transition ${
                            isPII
                              ? "border-danger/30 bg-danger/10 text-danger hover:bg-danger/20"
                              : "border-line bg-white text-ink-muted hover:border-brand hover:text-brand-dark"
                          }`}
                          title={isPII ? "Click to clear sensitivity flag" : "Click to mark column as sensitive"}
                        >
                          {isPII
                            ? <><Lock className="h-2.5 w-2.5" /> Sensitive</>
                            : <><ShieldOff className="h-2.5 w-2.5" /> Mark sensitive</>}
                        </button>
                        {isPII && (
                          <select
                            value={c.pii_category || "PII"}
                            onChange={(e) => onChangeCategory(e.target.value)}
                            className="rounded-md border border-line bg-white px-1.5 py-0.5 text-[10.5px] text-ink"
                          >
                            {PII_CATEGORIES.map(opt => (
                              <option key={opt.value} value={opt.value}>{opt.label}</option>
                            ))}
                          </select>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {tab === "preview" && preview && (
          <div className="overflow-x-auto">
            <table className="table-shell min-w-full">
              <thead>
                <tr>{preview.columns.map(col => <th key={col}>{col}</th>)}</tr>
              </thead>
              <tbody>
                {preview.rows.map((row, i) => (
                  <tr key={i}>
                    {preview.columns.map(col => (
                      <td key={col} className="whitespace-nowrap text-ink-muted">{String(row[col] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
};
