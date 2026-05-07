import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, FileOutput } from "lucide-react";
import { ConversionsApi, OutputApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill, Tabs,
} from "@/components/ui/Primitives";
import { confidenceTone } from "@/lib/utils";
import type {
  Conversion,
  OutputPreview,
} from "@/types";

export const OutputPreviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const [project, setProject] = useState<Conversion | null>(null);
  const [data, setData] = useState<OutputPreview | null>(null);
  const [tab, setTab] = useState("data");
  const [generating, setGenerating] = useState(false);

  const refresh = async () => {
    setData(null);
    OutputApi.preview(pid, 50).then(setData).catch(() => setData(null));
  };

  useEffect(() => {
    if (!pid) return;
    ConversionsApi.get(pid).then(setProject);
    refresh();
  }, [pid]);

  const generate = async () => {
    setGenerating(true);
    try { await OutputApi.generate(pid, "csv"); await refresh(); }
    finally { setGenerating(false); }
  };

  if (!project) return <PageLoader />;

  return (
    <>
      <Link to={`/projects/${pid}`} className="mb-3 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3 w-3" /> Back to Project
      </Link>
      <PageTitle
        title="Output Preview"
        subtitle={`${project.name} → ${project.template_name}`}
        right={<>
          <Button variant="secondary" onClick={generate} loading={generating}>
            <FileOutput className="h-4 w-4" /> Re-generate
          </Button>
          <a href={OutputApi.downloadUrl(pid)} target="_blank" rel="noreferrer" className="btn-primary">
            <Download className="h-4 w-4" /> Download CSV
          </a>
        </>}
      />

      <Card>
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "data", label: "Converted Data", count: data?.total_rows },
            { value: "lineage", label: "Lineage", count: data ? Object.keys(data.lineage).length : 0 },
          ]}
        />
        {data === null ? <PageLoader /> :
          tab === "data" ? (
            data.columns.length === 0 ? (
              <CardBody><EmptyState
                title="No converted output yet"
                description="Approve at least one mapping then click Re-generate."
              /></CardBody>
            ) : (
              <div className="overflow-x-auto">
                <table className="table-shell">
                  <thead>
                    <tr>
                      <th>#</th>
                      {data.columns.map(c => <th key={c} className="whitespace-nowrap">{c}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row, i) => (
                      <tr key={i}>
                        <td className="text-ink-muted">{i + 1}</td>
                        {data.columns.map(col => (
                          <td key={col} className="whitespace-nowrap text-ink-muted">{String(row[col] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Target Field</th><th>Source Column</th>
                  <th>Default</th><th>Rules Applied</th>
                  <th>Confidence</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.lineage).map(([target, lin]) => {
                  const tone = confidenceTone(lin.confidence);
                  return (
                    <tr key={target}>
                      <td className="font-medium">{target}</td>
                      <td>{lin.source_column ? <code className="rounded bg-canvas px-1.5 py-0.5 text-[12px]">{lin.source_column}</code> : <span className="text-ink-subtle">— (default)</span>}</td>
                      <td className="text-ink-muted">{lin.default_value || "—"}</td>
                      <td>
                        {(lin.rules || []).length === 0 ? <span className="text-ink-subtle">—</span> : (
                          <div className="flex flex-wrap gap-1">
                            {(lin.rules || []).map((r: any, i: number) =>
                              <Pill key={i} tone="brand">{r.rule_type}</Pill>)}
                          </div>
                        )}
                      </td>
                      <td className="font-mono text-xs tabular-nums">
                        <span className={
                          tone === "success" ? "text-success" :
                          tone === "warning" ? "text-warning" : "text-danger"
                        }>{Math.round(lin.confidence * 100)}%</span>
                      </td>
                      <td><Pill tone={lin.status === "approved" ? "success" : "neutral"}>{lin.status}</Pill></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        }
      </Card>
    </>
  );
};
