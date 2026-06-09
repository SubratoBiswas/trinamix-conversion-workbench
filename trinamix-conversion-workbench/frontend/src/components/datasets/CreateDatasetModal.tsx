import React, { useRef, useState } from "react";
import {
  Upload, Database, Cloud, Server, Layers, Search, Grid3x3, List,
  Plus, FileSpreadsheet, X, Briefcase,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Primitives";
import { DatasetsApi } from "@/api";
import type { DatasetDetail } from "@/types";

// Mocked connections — visually polished but flagged as "Connected" stubs.
// In a production deployment these would come from the backend.
const MOCK_CONNECTIONS = [
  { name: "Trinamix DB",     icon: Database, color: "bg-brand-subtle text-brand-dark",   subtitle: "Postgres · 12 schemas" },
  { name: "Oracle Fusion",   icon: Cloud,    color: "bg-info-subtle text-info",          subtitle: "Production · SCM Cloud" },
  { name: "Oracle EBS",      icon: Server,   color: "bg-info-subtle text-info",          subtitle: "Legacy · 11.5.10" },
  { name: "Workday HCM",     icon: Cloud,    color: "bg-success-subtle text-success",    subtitle: "REST · v40" },
  { name: "Salesforce CRM",  icon: Cloud,    color: "bg-info-subtle text-info",          subtitle: "Sandbox · API v59" },
  { name: "SAP S/4HANA",     icon: Server,   color: "bg-warning-subtle text-warning",    subtitle: "S4H · Quality" },
  { name: "Snowflake DW",    icon: Layers,   color: "bg-brand-subtle text-brand-dark",   subtitle: "PROD_WH · 8 dbs" },
];

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (ds: DatasetDetail) => void;
}

export const CreateDatasetModal: React.FC<Props> = ({ open, onClose, onCreated }) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [connStub, setConnStub] = useState<string | null>(null);

  if (!open) return null;

  const reset = () => { setPreview(null); setName(""); setDesc(""); setError(null); setConnStub(null); };

  const close = () => { reset(); onClose(); };

  const onPick = (f: File) => {
    setPreview(f);
    setName(f.name.replace(/\.[^/.]+$/, ""));
  };

  const submit = async () => {
    if (!preview) return;
    setBusy(true); setError(null);
    try {
      const ds = await DatasetsApi.upload(preview, name || undefined, desc || undefined);
      onCreated(ds);
      reset();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Upload failed");
    } finally { setBusy(false); }
  };

  const filtered = MOCK_CONNECTIONS.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4 backdrop-blur-sm" onClick={close}>
      <div className="w-full max-w-5xl rounded-xl bg-white shadow-soft" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between border-b border-line px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">Create Dataset</h2>
            <p className="mt-0.5 text-sm text-ink-muted">From a File, Subject Area, or Connection</p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="secondary" className="!h-9 !text-xs"><Plus className="h-3.5 w-3.5" /> Create Connection</Button>
            <button onClick={close} className="rounded p-1.5 text-ink-muted hover:bg-canvas hover:text-ink">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* If a file is staged, show the upload-confirmation pane */}
        {preview ? (
          <div className="px-6 py-6">
            <div className="mb-4 rounded-md border border-line bg-canvas px-4 py-3">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">
                  <FileSpreadsheet className="h-5 w-5" />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium text-ink">{preview.name}</div>
                  <div className="text-[11px] text-ink-muted">
                    {(preview.size / 1024).toFixed(1)} KB · ready to profile
                  </div>
                </div>
                <button
                  onClick={() => { setPreview(null); setName(""); }}
                  className="text-xs text-ink-muted hover:text-danger"
                >
                  Remove
                </button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Display name</label>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Legacy Item Master" />
              </div>
              <div>
                <label className="label">Description (optional)</label>
                <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Source system, snapshot date…" />
              </div>
            </div>
            {error && <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">{error}</div>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={close}>Cancel</Button>
              <Button onClick={submit} loading={busy}>Upload &amp; profile</Button>
            </div>
          </div>
        ) : connStub ? (
          // Connection stub state
          <div className="px-6 py-12 text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-warning-subtle text-warning">
              <Briefcase className="h-6 w-6" />
            </div>
            <h3 className="mt-4 text-base font-semibold text-ink">{connStub}</h3>
            <p className="mt-2 max-w-md mx-auto text-sm text-ink-muted">
              Connection-based dataset creation will be available once the data-source plugin
              is configured. For now, please use file upload to bring legacy extracts into the workbench.
            </p>
            <div className="mt-5 flex justify-center gap-2">
              <Button variant="secondary" onClick={() => setConnStub(null)}>← Back to sources</Button>
            </div>
          </div>
        ) : (
          <div className="px-6 py-5">
            {/* Search + view toggle */}
            <div className="mb-5 flex items-center gap-3">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
                <input
                  className="input !pl-9"
                  placeholder="Search connections…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="flex items-center rounded-md border border-line bg-white p-0.5">
                <button onClick={() => setView("grid")} className={cn("rounded p-1.5", view === "grid" ? "bg-canvas text-ink" : "text-ink-subtle")}>
                  <Grid3x3 className="h-3.5 w-3.5" />
                </button>
                <button onClick={() => setView("list")} className={cn("rounded p-1.5", view === "list" ? "bg-canvas text-ink" : "text-ink-subtle")}>
                  <List className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            {/* Source grid */}
            {view === "grid" ? (
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
                {/* File drop card — primary */}
                <div
                  onClick={() => inputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                  onDragLeave={() => setDrag(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDrag(false);
                    const f = e.dataTransfer.files?.[0];
                    if (f) onPick(f);
                  }}
                  className={cn(
                    "flex aspect-square cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-3 text-center transition",
                    drag ? "border-brand bg-brand-subtle" : "border-line bg-white hover:border-brand hover:bg-brand-subtle/40"
                  )}
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand text-white">
                    <Upload className="h-5 w-5" />
                  </div>
                  <div className="mt-2.5 text-[12px] font-semibold leading-tight text-ink">
                    Drop data file here or click to browse
                  </div>
                  <div className="mt-1 text-[10px] text-ink-muted">CSV · XLSX · XLS</div>
                </div>

                {/* Local subject area card */}
                <SourceCard
                  icon={Layers}
                  label="Local Subject Area"
                  color="bg-warning-subtle text-warning"
                  onClick={() => setConnStub("Local Subject Area")}
                />

                {/* Connection cards */}
                {filtered.map((c) => (
                  <SourceCard
                    key={c.name}
                    icon={c.icon}
                    label={c.name}
                    color={c.color}
                    onClick={() => setConnStub(c.name)}
                    subtitle={c.subtitle}
                  />
                ))}
              </div>
            ) : (
              <div className="overflow-hidden rounded-md border border-line">
                <table className="table-shell">
                  <thead><tr><th>Source</th><th>Type</th><th>Detail</th><th></th></tr></thead>
                  <tbody>
                    <tr onClick={() => inputRef.current?.click()} className="cursor-pointer">
                      <td><span className="inline-flex items-center gap-2 font-medium text-brand-dark"><Upload className="h-3.5 w-3.5" /> Drop data file here or click to browse</span></td>
                      <td><span className="text-ink-muted">File</span></td>
                      <td className="text-ink-muted">CSV · XLSX · XLS</td>
                      <td className="text-right"><span className="text-xs text-brand-dark">Upload →</span></td>
                    </tr>
                    {filtered.map((c) => (
                      <tr key={c.name} onClick={() => setConnStub(c.name)} className="cursor-pointer">
                        <td className="font-medium">{c.name}</td>
                        <td>Connection</td>
                        <td className="text-ink-muted">{c.subtitle}</td>
                        <td className="text-right"><span className="text-xs text-brand-dark">Connect →</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <input
              ref={inputRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onPick(f);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
};

const SourceCard: React.FC<{
  icon: React.ElementType;
  label: string;
  subtitle?: string;
  color: string;
  onClick: () => void;
}> = ({ icon: Icon, label, subtitle, color, onClick }) => (
  <button
    onClick={onClick}
    className="group flex aspect-square flex-col items-center justify-center rounded-lg border border-line bg-white px-3 text-center transition hover:border-brand hover:shadow-soft"
  >
    <div className={cn("flex h-10 w-10 items-center justify-center rounded-md", color)}>
      <Icon className="h-5 w-5" />
    </div>
    <div className="mt-2.5 line-clamp-2 text-[12px] font-medium leading-tight text-ink">{label}</div>
    {subtitle && <div className="mt-1 line-clamp-1 text-[10px] text-ink-muted">{subtitle}</div>}
  </button>
);
