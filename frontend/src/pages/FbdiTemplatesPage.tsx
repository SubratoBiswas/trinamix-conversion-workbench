import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Upload, FileSpreadsheet, ArrowLeft, Edit2, Save, X, Search,
} from "lucide-react";
import { FbdiApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, PageLoader,
  PageTitle, Pill, Tabs,
} from "@/components/ui/Primitives";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { cn, formatDate } from "@/lib/utils";
import type { FBDIField, FBDITemplate, FBDITemplateDetail } from "@/types";

// Module ordering matches Oracle's standard load sequence (config → master → tx)
const MODULE_ORDER = [
  "All", "GL", "LE", "CM", "AP", "TAX", "SCM", "HCM", "EXP",
  "AR", "PO", "OM", "FA", "PPM", "PAY", "MFG",
];
const TIER_ORDER = ["All", "T0", "T1", "T2", "T3"];

// Tier descriptions for the tooltip — explains the load-order taxonomy.
const TIER_HINT: Record<string, string> = {
  T0: "Configuration / setup data — must precede master",
  T1: "Master data",
  T2: "Open transactions",
  T3: "History / period-end balances",
};

const PHASE_TONE: Record<string, "info" | "warning" | "brand" | "success"> = {
  Blueprint:  "info",
  Build:      "warning",
  Validation: "brand",
  Cutover:    "success",
};

export const FbdiTemplatesPage: React.FC = () => {
  const [items, setItems] = useState<FBDITemplate[] | null>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [module, setModule] = useState("SCM");
  const [bo, setBo] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [search, setSearch] = useState("");
  const [moduleFilter, setModuleFilter] = useState("All");
  const [tierFilter, setTierFilter] = useState("All");

  const refresh = () => FbdiApi.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const submit = async () => {
    if (!file) return;
    setUploading(true); setError(null);
    try {
      await FbdiApi.upload(file, { name: name || undefined, module: module || undefined, business_object: bo || undefined });
      setOpen(false); setFile(null); setName(""); setBo("");
      refresh();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Upload failed");
    } finally { setUploading(false); }
  };

  // Apply filters
  const visible = useMemo(() => {
    if (!items) return [];
    const term = search.trim().toLowerCase();
    return items.filter((t) => {
      if (moduleFilter !== "All" && t.module !== moduleFilter) return false;
      if (tierFilter !== "All" && t.tier !== tierFilter) return false;
      if (term && !(
        t.name.toLowerCase().includes(term) ||
        (t.business_object || "").toLowerCase().includes(term) ||
        (t.description || "").toLowerCase().includes(term)
      )) return false;
      return true;
    });
  }, [items, search, moduleFilter, tierFilter]);

  return (
    <>
      <PageTitle
        title="FBDI Manifest"
        subtitle={
          items
            ? `v2.0 · ${items.length} templates across ${countModules(items)} modules`
            : undefined
        }
        right={<Button onClick={() => setOpen(true)}><Upload className="h-4 w-4" /> Upload Template</Button>}
      />

      <Card>
        {/* Filter bar */}
        <div className="border-b border-line px-4 py-3">
          {/* Search */}
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
            <input
              className="input !pl-9"
              placeholder="Search templates by name, business object, or description…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {/* Module pills */}
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {MODULE_ORDER.map((m) => (
              <FilterPill
                key={m}
                label={m}
                active={moduleFilter === m}
                onClick={() => setModuleFilter(m)}
                count={m === "All" ? items?.length : items?.filter((t) => t.module === m).length}
              />
            ))}
          </div>

          {/* Tier pills */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Tier:</span>
            {TIER_ORDER.map((t) => (
              <FilterPill
                key={t}
                label={t}
                active={tierFilter === t}
                onClick={() => setTierFilter(t)}
                title={TIER_HINT[t]}
                count={t === "All" ? items?.length : items?.filter((tpl) => tpl.tier === t).length}
                variant="tier"
              />
            ))}
            <span className="ml-auto text-[11px] text-ink-muted">
              {visible.length} result{visible.length === 1 ? "" : "s"}
            </span>
          </div>
        </div>

        {/* Manifest grid */}
        {items === null ? <PageLoader /> :
          visible.length === 0 ? (
            <CardBody>
              <EmptyState
                icon={<FileSpreadsheet className="h-5 w-5" />}
                title={items.length === 0 ? "No FBDI templates yet" : "No templates match the filters"}
                description={
                  items.length === 0
                    ? "Upload an Oracle FBDI .xlsm/.xlsx file or seed the manifest catalogue."
                    : "Adjust the module/tier filters or search term."
                }
                action={items.length === 0 ? (
                  <Button onClick={() => setOpen(true)}><Upload className="h-4 w-4" /> Upload Template</Button>
                ) : undefined}
              />
            </CardBody>
          ) : (
            <table className="table-shell">
              <thead>
                <tr>
                  <th>Template</th>
                  <th className="!w-20">Module</th>
                  <th className="!w-16">Tier</th>
                  <th className="!w-24">Phase</th>
                  <th className="!w-28">Required Fields</th>
                  <th className="!w-24">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <Link to={`/fbdi/${t.id}`} className="font-mono text-[12.5px] text-ink hover:text-brand-dark">
                        {t.name}
                      </Link>
                      {t.business_object && (
                        <div className="mt-0.5 text-[10.5px] text-ink-muted">→ {t.business_object}</div>
                      )}
                    </td>
                    <td><Pill tone="brand">{t.module || "—"}</Pill></td>
                    <td>
                      <span title={TIER_HINT[t.tier]} className="font-mono text-[11px] text-ink-muted">
                        {t.tier}
                      </span>
                    </td>
                    <td><Pill tone={PHASE_TONE[t.phase] || "neutral"}>{t.phase}</Pill></td>
                    <td className="font-mono tabular-nums text-[12px] text-ink-muted">
                      {t.required_field_count} required
                    </td>
                    <td>
                      <Pill tone={t.status === "parsed" ? "success" : "neutral"}>
                        {t.status === "parsed" ? "complete" : t.status}
                      </Pill>
                    </td>
                    <td className="text-right">
                      <Link to={`/fbdi/${t.id}`} className="btn-ghost h-7 px-2 text-xs">Open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Upload FBDI Template"
        size="md"
        footer={<>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={submit} loading={uploading} disabled={!file}>Upload &amp; parse</Button>
        </>}
      >
        <div className="space-y-4">
          <FileDropzone accept=".xlsx,.xlsm,.xls" helper="Oracle FBDI .xlsm/.xlsx — fields, types and required flags will be auto-extracted." onFile={setFile} />
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Display name</label>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Item Master (SCM)" />
            </div>
            <div>
              <label className="label">Module</label>
              <select className="input" value={module} onChange={(e) => setModule(e.target.value)}>
                {MODULE_ORDER.filter(m => m !== "All").map((m) => <option key={m}>{m}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="label">Business Object</label>
            <input className="input" value={bo} onChange={(e) => setBo(e.target.value)} placeholder="e.g. Item, Customer, Sales Order" />
          </div>
          {error && <div className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">{error}</div>}
        </div>
      </Modal>
    </>
  );
};

// ─────── Filter pill ───────

const FilterPill: React.FC<{
  label: string; active: boolean; onClick: () => void;
  count?: number; title?: string; variant?: "module" | "tier";
}> = ({ label, active, onClick, count, title, variant = "module" }) => (
  <button
    onClick={onClick}
    title={title}
    className={cn(
      "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition",
      active
        ? variant === "tier"
          ? "border-brand bg-brand text-white"
          : "border-success bg-success-subtle text-success"
        : "border-line bg-white text-ink-muted hover:border-ink-subtle hover:text-ink",
    )}
  >
    <span className={variant === "tier" ? "font-mono" : ""}>{label}</span>
    {typeof count === "number" && count > 0 && !active && (
      <span className="text-[9.5px] tabular-nums text-ink-subtle">{count}</span>
    )}
  </button>
);

function countModules(items: FBDITemplate[]): number {
  return new Set(items.map((t) => t.module).filter(Boolean)).size;
}

// -------- Detail page --------

export const FbdiTemplateDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [tpl, setTpl] = useState<FBDITemplateDetail | null>(null);
  const [fields, setFields] = useState<FBDIField[]>([]);
  const [tab, setTab] = useState("fields");
  const [editing, setEditing] = useState<FBDIField | null>(null);

  const loadAll = () => {
    if (!id) return;
    FbdiApi.get(Number(id)).then(setTpl);
    FbdiApi.fields(Number(id)).then(setFields);
  };
  useEffect(() => { loadAll(); }, [id]);

  if (!tpl) return <PageLoader />;
  const requiredCount = fields.filter(f => f.required).length;

  return (
    <>
      <Link to="/fbdi" className="mb-3 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3 w-3" /> Back to FBDI Templates
      </Link>
      <PageTitle
        title={tpl.name}
        subtitle={`${tpl.module || "—"} · ${tpl.business_object || "—"} · ${fields.length} fields, ${requiredCount} required`}
        right={<Pill tone="success">{tpl.status}</Pill>}
      />

      <Card>
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "fields", label: "Fields", count: fields.length },
            { value: "sheets", label: "Sheets", count: tpl.sheets.length },
          ]}
        />
        {tab === "fields" && (
          <div className="overflow-x-auto">
            <table className="table-shell">
              <thead>
                <tr>
                  <th>#</th><th>Field</th><th>Required</th>
                  <th>Type</th><th className="text-right">Length</th>
                  <th>Description</th><th>Modules</th><th></th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={f.id}>
                    <td className="text-ink-muted">{f.sequence}</td>
                    <td className="font-medium text-ink">{f.field_name}</td>
                    <td>{f.required ? <Pill tone="danger">required</Pill> : <Pill tone="neutral">optional</Pill>}</td>
                    <td>{f.data_type || "—"}</td>
                    <td className="text-right tabular-nums text-ink-muted">{f.max_length ?? "—"}</td>
                    <td className="max-w-[420px] truncate text-ink-muted" title={f.description || ""}>{f.description || "—"}</td>
                    <td className="text-ink-muted text-xs">{(f.required_modules || []).slice(0, 2).join(", ") || "—"}</td>
                    <td className="text-right">
                      <button onClick={() => setEditing(f)} className="btn-ghost h-7 px-2 text-xs">
                        <Edit2 className="h-3.5 w-3.5" /> Edit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab === "sheets" && (
          <table className="table-shell">
            <thead><tr><th>#</th><th>Sheet</th><th className="text-right">Fields</th></tr></thead>
            <tbody>
              {tpl.sheets.map(s => (
                <tr key={s.id}>
                  <td className="text-ink-muted">{s.sequence + 1}</td>
                  <td className="font-medium">{s.sheet_name}</td>
                  <td className="text-right tabular-nums">{s.field_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {editing && <EditFieldModal field={editing} onClose={() => setEditing(null)} onSaved={loadAll} />}
    </>
  );
};

const EditFieldModal: React.FC<{ field: FBDIField; onClose: () => void; onSaved: () => void }> = ({ field, onClose, onSaved }) => {
  const [f, setF] = useState<FBDIField>(field);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await FbdiApi.updateField(field.id, {
        field_name: f.field_name, display_name: f.display_name, description: f.description,
        required: f.required, data_type: f.data_type, max_length: f.max_length,
        format_mask: f.format_mask, sample_value: f.sample_value, lookup_type: f.lookup_type,
        validation_notes: f.validation_notes,
      });
      onSaved(); onClose();
    } finally { setSaving(false); }
  };

  return (
    <Modal open onClose={onClose} title={`Edit field: ${field.field_name}`} size="lg" footer={<>
      <Button variant="secondary" onClick={onClose}><X className="h-4 w-4" /> Cancel</Button>
      <Button onClick={save} loading={saving}><Save className="h-4 w-4" /> Save</Button>
    </>}>
      <div className="grid grid-cols-2 gap-4">
        <div><label className="label">Field name</label><input className="input" value={f.field_name} onChange={(e) => setF({ ...f, field_name: e.target.value })} /></div>
        <div><label className="label">Data type</label><input className="input" value={f.data_type || ""} onChange={(e) => setF({ ...f, data_type: e.target.value })} /></div>
        <div><label className="label">Max length</label><input className="input" type="number" value={f.max_length ?? ""} onChange={(e) => setF({ ...f, max_length: e.target.value ? Number(e.target.value) : null })} /></div>
        <div><label className="label">Format mask</label><input className="input" value={f.format_mask || ""} onChange={(e) => setF({ ...f, format_mask: e.target.value })} /></div>
        <div><label className="label">Sample value</label><input className="input" value={f.sample_value || ""} onChange={(e) => setF({ ...f, sample_value: e.target.value })} /></div>
        <div><label className="label">Lookup type</label><input className="input" value={f.lookup_type || ""} onChange={(e) => setF({ ...f, lookup_type: e.target.value })} /></div>
        <div className="col-span-2">
          <label className="label">Description</label>
          <textarea className="input min-h-[60px]" value={f.description || ""} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </div>
        <div className="col-span-2">
          <label className="label">Validation notes</label>
          <textarea className="input min-h-[60px]" value={f.validation_notes || ""} onChange={(e) => setF({ ...f, validation_notes: e.target.value })} />
        </div>
        <label className="col-span-2 inline-flex items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 rounded border-line text-brand focus:ring-brand"
            checked={f.required} onChange={(e) => setF({ ...f, required: e.target.checked })} />
          Required field
        </label>
      </div>
    </Modal>
  );
};
