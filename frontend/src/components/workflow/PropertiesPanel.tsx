import React, { useEffect, useMemo, useState } from "react";
import { Node } from "reactflow";
import {
  X, Table2, Settings as SettingsIcon, Sparkles, Trash2, Eye,
  Type, Hash, Calendar, ToggleLeft,
} from "lucide-react";
import { NODE_TYPES, type NodeFieldSchema } from "./NodeRegistry";
import { Button, Spinner } from "@/components/ui/Primitives";
import { DatasetsApi, FbdiApi } from "@/api";
import { cn } from "@/lib/utils";
import type { Dataset, DatasetPreview, FBDITemplate, FBDIField } from "@/types";

interface Props {
  node: Node | null;
  onClose: () => void;
  onChange: (nodeId: string, data: Record<string, any>) => void;
  onDelete: (nodeId: string) => void;
  /** Called to fetch upstream data preview (for the data-preview tab). */
  upstreamDatasetId?: number | null;
  /** Resolution map: id -> name, used for picker labels. */
  datasets: Dataset[];
  templates: FBDITemplate[];
}

const TYPE_ABBR: Record<string, { abbr: string; icon: React.ElementType; tone: string }> = {
  string:  { abbr: "ab", icon: Type,       tone: "text-info" },
  integer: { abbr: "99", icon: Hash,       tone: "text-success" },
  float:   { abbr: "1.2", icon: Hash,      tone: "text-success" },
  date:    { abbr: "📅",  icon: Calendar,  tone: "text-warning" },
  boolean: { abbr: "T/F", icon: ToggleLeft, tone: "text-brand-dark" },
};

export const PropertiesPanel: React.FC<Props> = ({
  node, onClose, onChange, onDelete,
  upstreamDatasetId, datasets, templates,
}) => {
  const [tab, setTab] = useState<"properties" | "preview">("properties");
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [fbdiFields, setFbdiFields] = useState<FBDIField[]>([]);

  const def = node ? NODE_TYPES[node.data?.nodeType] : null;
  const Icon = def?.icon;

  // When switching to preview tab, fetch upstream dataset preview
  useEffect(() => {
    if (tab !== "preview" || !node) return;
    // For dataset nodes, preview is the dataset itself; for downstream, use upstream.
    const targetId = node.data?.nodeType === "dataset" ? node.data?.datasetId : upstreamDatasetId;
    if (!targetId) { setPreview(null); return; }
    setPreviewing(true);
    DatasetsApi.preview(Number(targetId), 30)
      .then(setPreview)
      .finally(() => setPreviewing(false));
  }, [tab, node, upstreamDatasetId]);

  // For FBDI Target nodes, fetch the field list to summarise required count
  useEffect(() => {
    if (node?.data?.nodeType === "fbdi_template" && node.data?.templateId) {
      FbdiApi.fields(Number(node.data.templateId)).then(setFbdiFields);
    } else {
      setFbdiFields([]);
    }
  }, [node?.data?.nodeType, node?.data?.templateId]);

  if (!node || !def) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs text-ink-muted">
        <div>
          <Eye className="mx-auto mb-2 h-5 w-5 text-ink-subtle" />
          Select a node on the canvas to see its properties and data preview.
        </div>
      </div>
    );
  }

  const update = (k: string, v: any) => onChange(node.id, { [k]: v });

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-line bg-white px-4 py-2.5">
        {Icon && (
          <div className={cn("flex h-7 w-7 items-center justify-center rounded-md border", def.bg, def.accent)}>
            <Icon className="h-3.5 w-3.5" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-ink-muted">{def.label}</div>
          <div className="truncate text-sm font-semibold text-ink">{node.data?.label || def.label}</div>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center rounded-md border border-line bg-white p-0.5">
          <button onClick={() => setTab("properties")}
            className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium",
              tab === "properties" ? "bg-canvas text-ink" : "text-ink-muted hover:text-ink")}>
            <SettingsIcon className="h-3 w-3" /> Properties
          </button>
          <button onClick={() => setTab("preview")}
            className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium",
              tab === "preview" ? "bg-canvas text-ink" : "text-ink-muted hover:text-ink")}>
            <Table2 className="h-3 w-3" /> Data Preview
          </button>
        </div>

        <Button variant="ghost" className="!h-8 !px-2 !text-xs text-danger hover:!bg-danger-subtle"
          onClick={() => onDelete(node.id)}>
          <Trash2 className="h-3.5 w-3.5" /> Delete
        </Button>
        <button onClick={onClose} className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Body */}
      {tab === "properties" ? (
        <div className="flex-1 overflow-y-auto p-4">
          <p className="mb-4 max-w-2xl text-xs text-ink-muted">{def.description}</p>

          {/* Label is always editable */}
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="label">Display label</label>
              <input
                className="input !text-sm"
                value={node.data?.label || ""}
                onChange={(e) => update("label", e.target.value)}
                placeholder={def.label}
              />
            </div>

            {(def.fields || []).map((f) => (
              <div key={f.key} className={f.kind === "textarea" || f.kind === "json" ? "col-span-2" : ""}>
                <FieldInput
                  field={f}
                  value={node.data?.[f.key]}
                  onChange={(v) => update(f.key, v)}
                  datasets={datasets}
                  templates={templates}
                />
              </div>
            ))}
          </div>

          {/* FBDI target summary panel */}
          {node.data?.nodeType === "fbdi_template" && fbdiFields.length > 0 && (
            <div className="mt-5 rounded-md border border-line bg-canvas p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">Target metadata</div>
              <div className="mt-2 grid grid-cols-3 gap-3 text-xs">
                <div>
                  <div className="text-[10px] text-ink-muted">Total fields</div>
                  <div className="text-base font-semibold tabular-nums">{fbdiFields.length}</div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">Required</div>
                  <div className="text-base font-semibold tabular-nums text-danger">
                    {fbdiFields.filter(f => f.required).length}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">Optional</div>
                  <div className="text-base font-semibold tabular-nums">
                    {fbdiFields.filter(f => !f.required).length}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Run-status detail */}
          {node.data?.detail && (
            <div className="mt-5 rounded-md border border-info/30 bg-info-subtle p-3 text-xs text-ink">
              <div className="flex items-center gap-1.5 font-semibold text-info">
                <Sparkles className="h-3 w-3" /> Last run
              </div>
              <div className="mt-1">{node.data.detail}</div>
            </div>
          )}
        </div>
      ) : (
        <DataPreviewTab preview={preview} loading={previewing} />
      )}
    </div>
  );
};

// ─────── Field input dispatcher ───────

const FieldInput: React.FC<{
  field: NodeFieldSchema;
  value: any;
  onChange: (v: any) => void;
  datasets: Dataset[];
  templates: FBDITemplate[];
}> = ({ field, value, onChange, datasets, templates }) => {
  const Label = (
    <label className="label">
      {field.label}
      {field.required && <span className="ml-1 text-danger">*</span>}
    </label>
  );

  const helper = field.helper && (
    <p className="mt-1 text-[10.5px] text-ink-muted">{field.helper}</p>
  );

  switch (field.kind) {
    case "text":
      return <>{Label}<input className="input !text-sm" placeholder={field.placeholder} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />{helper}</>;
    case "number":
      return <>{Label}<input type="number" className="input !text-sm" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />{helper}</>;
    case "textarea":
      return <>{Label}<textarea className="input !text-sm min-h-[80px] font-mono" placeholder={field.placeholder} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />{helper}</>;
    case "select":
      return <>{Label}<select className="input !text-sm" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {(field.options || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>{helper}</>;
    case "multiselect":
      return <>{Label}<input className="input !text-sm" placeholder="Comma-separated" value={Array.isArray(value) ? value.join(", ") : (value ?? "")} onChange={(e) => onChange(e.target.value.split(",").map(s => s.trim()).filter(Boolean))} />{helper}</>;
    case "json":
      return <>{Label}<textarea className="input !text-sm min-h-[80px] font-mono"
        value={typeof value === "string" ? value : JSON.stringify(value || {}, null, 2)}
        onChange={(e) => {
          try { onChange(JSON.parse(e.target.value || "{}")); } catch { onChange(e.target.value); }
        }} />{helper}</>;
    case "datasetPicker":
      return <>{Label}<select className="input !text-sm" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— pick a dataset —</option>
        {datasets.map(d => <option key={d.id} value={d.id}>{d.name} ({d.row_count.toLocaleString()} rows)</option>)}
      </select>{helper}</>;
    case "templatePicker":
      return <>{Label}<select className="input !text-sm" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">— pick a template —</option>
        {templates.map(t => <option key={t.id} value={t.id}>{t.name} {t.business_object ? `· ${t.business_object}` : ""}</option>)}
      </select>{helper}</>;
    case "columnsPicker":
      return <>{Label}<input className="input !text-sm" placeholder="Comma-separated column names" value={Array.isArray(value) ? value.join(", ") : (value ?? "")} onChange={(e) => onChange(e.target.value.split(",").map(s => s.trim()).filter(Boolean))} />{helper}</>;
  }
};

// ─────── Data preview tab ───────

const DataPreviewTab: React.FC<{ preview: DatasetPreview | null; loading: boolean }> = ({ preview, loading }) => {
  if (loading) return (
    <div className="flex flex-1 items-center justify-center gap-2 text-xs text-ink-muted">
      <Spinner /> Loading preview…
    </div>
  );
  if (!preview || preview.columns.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center text-xs text-ink-muted">
        <div>
          <Table2 className="mx-auto mb-2 h-5 w-5 text-ink-subtle" />
          No data to preview. Connect a Dataset node upstream.
        </div>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-auto">
      <table className="table-shell">
        <thead>
          <tr>
            <th className="!w-10">#</th>
            {preview.columns.map((c) => {
              const meta = TYPE_ABBR.string; // Default — preview doesn't ship types per column
              return (
                <th key={c} className="!font-mono">
                  <span className="mr-1 text-ink-subtle">{meta.abbr}</span>{c}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, i) => (
            <tr key={i}>
              <td className="font-mono text-[11px] text-ink-subtle">{i + 1}</td>
              {preview.columns.map((c) => (
                <td key={c} className="whitespace-nowrap font-mono text-[12px]">
                  {row[c] == null || row[c] === "" ?
                    <span className="italic text-ink-subtle">null</span> :
                    String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
