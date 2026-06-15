import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Database, Plus, Eye, Sparkles, Search, Grid3x3, List as ListIcon, Wand2 } from "lucide-react";
import { DatasetsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { CreateDatasetModal } from "@/components/datasets/CreateDatasetModal";
import { formatDate, cn } from "@/lib/utils";
import type { Dataset } from "@/types";

export const DatasetsPage: React.FC = () => {
  const [items, setItems] = useState<Dataset[] | null>(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const nav = useNavigate();

  const refresh = () => DatasetsApi.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const filtered = items?.filter((d) =>
    !search || d.name.toLowerCase().includes(search.toLowerCase()) || d.file_name.toLowerCase().includes(search.toLowerCase())
  ) || [];

  return (
    <>
      <PageTitle
        title="Datasets"
        subtitle="Legacy source extracts available for conversion"
        right={
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" /> Create Dataset
          </Button>
        }
      />

      {/* Toolbar */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
          <input
            className="input !pl-9"
            placeholder="Search datasets…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center rounded-md border border-line bg-white p-0.5">
          <button onClick={() => setView("grid")} className={cn("rounded p-1.5", view === "grid" ? "bg-canvas text-ink" : "text-ink-subtle")} title="Grid">
            <Grid3x3 className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setView("list")} className={cn("rounded p-1.5", view === "list" ? "bg-canvas text-ink" : "text-ink-subtle")} title="List">
            <ListIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {items === null ? <PageLoader /> :
        items.length === 0 ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<Database className="h-5 w-5" />}
                title="No datasets uploaded yet"
                description="Upload a CSV or Excel extract from your legacy system to begin profiling and mapping."
                action={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Create Dataset</Button>}
              />
            </CardBody>
          </Card>
        ) : view === "grid" ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filtered.map((d) => (
              <button
                key={d.id}
                onClick={() => nav(`/datasets/${d.id}/prepare`)}
                className="group flex flex-col items-start rounded-lg border border-line bg-white px-4 py-4 text-left transition hover:border-brand hover:shadow-soft"
              >
                <div className="flex w-full items-start justify-between">
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">
                    <Database className="h-5 w-5" />
                  </div>
                  <Pill tone="success">{d.status}</Pill>
                </div>
                <div className="mt-3 line-clamp-2 text-sm font-semibold text-ink">{d.name}</div>
                <div className="mt-1 line-clamp-1 font-mono text-[11px] text-ink-muted">{d.file_name}</div>
                <div className="mt-3 flex items-center gap-3 text-[11px] text-ink-muted">
                  <span><span className="font-semibold text-ink">{d.row_count.toLocaleString()}</span> rows</span>
                  <span>·</span>
                  <span><span className="font-semibold text-ink">{d.column_count}</span> cols</span>
                  <span>·</span>
                  <span>{d.file_type.toUpperCase()}</span>
                </div>
                <div className="mt-3 flex w-full items-center justify-between border-t border-line pt-2 text-[11px] text-ink-muted">
                  <span>Updated {formatDate(d.uploaded_at)}</span>
                  <span className="inline-flex items-center gap-1 text-brand-dark opacity-0 transition group-hover:opacity-100">
                    <Wand2 className="h-3 w-3" /> Prepare →
                  </span>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <Card>
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Name</th><th>File</th><th>Type</th>
                  <th className="text-right">Rows</th><th className="text-right">Cols</th>
                  <th>Status</th><th>Uploaded</th><th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((d) => (
                  <tr key={d.id} onClick={() => nav(`/datasets/${d.id}/prepare`)} className="cursor-pointer">
                    <td className="font-medium text-ink">{d.name}</td>
                    <td className="font-mono text-[11px] text-ink-muted">{d.file_name}</td>
                    <td><Pill tone="neutral">{d.file_type.toUpperCase()}</Pill></td>
                    <td className="text-right tabular-nums">{d.row_count.toLocaleString()}</td>
                    <td className="text-right tabular-nums">{d.column_count}</td>
                    <td><Pill tone="success">{d.status}</Pill></td>
                    <td className="text-ink-muted">{formatDate(d.uploaded_at)}</td>
                    <td className="text-right">
                      <button className="btn-ghost h-7 px-2 text-xs" onClick={(e) => { e.stopPropagation(); nav(`/datasets/${d.id}/prepare`); }}>
                        <Wand2 className="h-3.5 w-3.5" /> Prepare
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )
      }

      <CreateDatasetModal
        open={open}
        onClose={() => setOpen(false)}
        onCreated={(ds) => {
          setOpen(false);
          refresh();
          // Route directly to prep page so the user sees the OAC-style flow
          nav(`/datasets/${ds.id}/prepare`);
        }}
      />
    </>
  );
};
