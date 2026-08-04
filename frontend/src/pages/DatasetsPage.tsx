import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Database, Plus, Eye, Sparkles, Search, Grid3x3, List as ListIcon, Wand2, Trash2, Loader2, Download } from "lucide-react";
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

  const [deleting, setDeleting] = useState<string | null>(null);
  const [wiping, setWiping] = useState(false);

  const refresh = () => DatasetsApi.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const handleDelete = async (d: Dataset) => {
    const cc = d.conversion_count || 0;
    const names = (d.conversion_names || []).join(", ");
    const msg = cc > 0
      ? `Delete "${d.name}" AND its ${cc} conversion${cc === 1 ? "" : "s"}?\n\n${names}\n\nThis can't be undone.`
      : `Delete "${d.name}"?\n\nThis permanently removes the dataset and its profile. This can't be undone.`;
    if (!window.confirm(msg)) return;
    setDeleting(String(d.id));
    try {
      await DatasetsApi.delete(String(d.id), cc > 0);
      setItems((prev) => (prev ? prev.filter((x) => x.id !== d.id) : prev));
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Could not delete this dataset.");
    } finally {
      setDeleting(null);
    }
  };

  const deleteAll = async () => {
    if (!items || items.length === 0) return;
    const total = items.length;
    const convTotal = items.reduce((n, d) => n + (d.conversion_count || 0), 0);
    if (!window.confirm(
      `Delete ALL ${total} dataset${total === 1 ? "" : "s"} and every conversion that uses them (${convTotal} conversion${convTotal === 1 ? "" : "s"})?\n\nThis cannot be undone.`
    )) return;
    setWiping(true);
    try {
      for (const d of [...items]) {
        await DatasetsApi.delete(String(d.id), true).catch(() => {});
      }
      await refresh();
    } finally {
      setWiping(false);
    }
  };

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
        {items && items.length > 0 && (
          <button
            onClick={deleteAll}
            disabled={wiping}
            title="Delete every dataset and the conversions that use them"
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            {wiping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            {wiping ? "Deleting…" : "Delete all"}
          </button>
        )}
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
              <div
                key={d.id}
                role="button"
                tabIndex={0}
                onClick={() => nav(`/datasets/${d.id}/prepare`)}
                onKeyDown={(e) => { if (e.key === "Enter") nav(`/datasets/${d.id}/prepare`); }}
                className="group flex cursor-pointer flex-col items-start rounded-lg border border-line bg-white px-4 py-4 text-left transition hover:border-brand hover:shadow-soft"
              >
                <div className="flex w-full items-start justify-between">
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">
                    <Database className="h-5 w-5" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Pill tone="success">{d.status}</Pill>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(d); }}
                      disabled={deleting === String(d.id)}
                      title="Delete dataset"
                      className="rounded p-1 text-ink-subtle opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <div className="mt-3 line-clamp-2 text-sm font-semibold text-ink">{d.name}</div>
                <div className="mt-1 line-clamp-1 font-mono text-[11px] text-ink-muted">{d.file_name}</div>
                {d.conversion_names && d.conversion_names.length > 0 && (
                  <div className="mt-1 line-clamp-1 text-[11px] text-brand-dark" title={d.conversion_names.join(", ")}>
                    ↳ {d.conversion_names.join(", ")}
                  </div>
                )}
                <div className="mt-3 flex items-center gap-3 text-[11px] text-ink-muted">
                  <span><span className="font-semibold text-ink">{d.row_count.toLocaleString()}</span> rows</span>
                  <span>·</span>
                  <span><span className="font-semibold text-ink">{d.column_count}</span> cols</span>
                  <span>·</span>
                  <span>{d.file_type.toUpperCase()}</span>
                </div>
                <div className="mt-3 flex w-full items-center justify-between border-t border-line pt-2 text-[11px] text-ink-muted">
                  <span>Updated {formatDate(d.uploaded_at)}</span>
                  <span className="inline-flex items-center gap-2">
                    {/* The source file, back out of the tool. It was reachable
                        only from a conversion's own card, so a dataset not yet
                        bound to one -- or one you are looking at HERE, which is
                        where you look when the row/column counts are the
                        question -- could not be opened at all. */}
                    <button
                      className="inline-flex items-center gap-1 rounded px-1 text-brand-dark hover:underline"
                      title="Download the source file exactly as it was uploaded"
                      onClick={(e) => { e.stopPropagation(); e.preventDefault();
                        void DatasetsApi.download(String(d.id), d.file_name || d.name); }}
                    >
                      <Download className="h-3 w-3" /> Download
                    </button>
                    <span className="inline-flex items-center gap-1 text-brand-dark opacity-0 transition group-hover:opacity-100">
                      <Wand2 className="h-3 w-3" /> Prepare →
                    </span>
                  </span>
                </div>
              </div>
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
                    <td className="font-medium text-ink">
                      {d.name}
                      {d.conversion_names && d.conversion_names.length > 0 && (
                        <div className="font-normal text-[11px] text-brand-dark" title={d.conversion_names.join(", ")}>
                          ↳ {d.conversion_names.join(", ")}
                        </div>
                      )}
                    </td>
                    <td className="font-mono text-[11px] text-ink-muted">{d.file_name}</td>
                    <td><Pill tone="neutral">{d.file_type.toUpperCase()}</Pill></td>
                    <td className="text-right tabular-nums">{d.row_count.toLocaleString()}</td>
                    <td className="text-right tabular-nums">{d.column_count}</td>
                    <td><Pill tone="success">{d.status}</Pill></td>
                    <td className="text-ink-muted">{formatDate(d.uploaded_at)}</td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button className="btn-ghost h-7 px-2 text-xs" onClick={(e) => { e.stopPropagation(); nav(`/datasets/${d.id}/prepare`); }}>
                          <Wand2 className="h-3.5 w-3.5" /> Prepare
                        </button>
                        <button
                          className="btn-ghost h-7 px-2 text-xs"
                          title="Download the source file exactly as it was uploaded"
                          onClick={(e) => { e.stopPropagation();
                            void DatasetsApi.download(String(d.id), d.file_name || d.name); }}
                        >
                          <Download className="h-3.5 w-3.5" />
                        </button>
                        <button
                          className="btn-ghost h-7 px-2 text-xs text-red-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                          disabled={deleting === String(d.id)}
                          title="Delete dataset"
                          onClick={(e) => { e.stopPropagation(); handleDelete(d); }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
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
