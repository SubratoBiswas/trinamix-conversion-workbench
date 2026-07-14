import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Award, CheckCircle2, CircleAlert, Pencil, RefreshCw, Trash2, Upload, X,
} from "lucide-react";
import {
  FbdiApi, GoldApi,
  type FBDITemplate, type GoldOrphan, type GoldStandard, type GoldUploadResult,
} from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatNumber } from "@/lib/utils";

/**
 * Gold Standards — a project-independent library, like Templates.
 *
 * A client's approved FBDI output describes how an OBJECT should look, not how one
 * engagement went. Uploading it here derives object-level rules (constant defaults,
 * the columns gold deliberately leaves blank, and — when a paired source extract is
 * supplied — source→target column mappings) that every conversion of that object
 * applies automatically at generate. No project required.
 */

const STATUS: Record<GoldStandard["status"], { tone: "success" | "warning" | "danger"; label: string }> = {
  learned: { tone: "success", label: "Learned" },
  unmatched: { tone: "warning", label: "No template match" },
  error: { tone: "danger", label: "Failed" },
};

const UploadModal: React.FC<{
  open: boolean;
  templates: FBDITemplate[];
  onClose: () => void;
  onDone: () => void;
}> = ({ open, templates, onClose, onDone }) => {
  const [golds, setGolds] = useState<File[]>([]);
  const [source, setSource] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<GoldUploadResult | null>(null);
  const goldRef = useRef<HTMLInputElement>(null);
  const srcRef = useRef<HTMLInputElement>(null);

  const single = golds.length === 1;

  useEffect(() => {
    if (open) {
      setGolds([]); setSource(null); setName(""); setTemplateId("");
      setErr(null); setResult(null);
    }
  }, [open]);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setGolds(prev => {
      // Same file picked twice (easy to do across two dialogs) shouldn't upload twice.
      const seen = new Set(prev.map(f => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter(f => !seen.has(`${f.name}:${f.size}`))];
    });
    setResult(null);
  };

  const submit = async () => {
    if (!golds.length) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await GoldApi.upload(golds, {
        name: name || undefined,
        templateId: templateId || undefined,
        sourceFile: source || undefined,
      });
      setResult(r);
      onDone();
      // Anything unmatched or failed deserves a look, so hold the dialog open.
      if (r.unmatched === 0 && r.failed === 0) onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={golds.length > 1 ? `Add ${golds.length} gold standards` : "Add a gold standard"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>{result ? "Done" : "Cancel"}</Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!golds.length}>
            Upload &amp; learn{golds.length > 1 ? ` (${golds.length})` : ""}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          Upload the FBDI outputs the client has already approved — one, or the whole
          set. A supplier load is six files, and each one identifies its own template
          from its headers, so they can all go in together. No project needed.
        </p>

        <div>
          <div className="mb-1 text-xs font-semibold text-ink">Approved FBDI output(s)</div>
          <input
            ref={goldRef}
            type="file"
            multiple
            accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt"
            className="hidden"
            onChange={e => { addFiles(e.target.files); e.target.value = ""; }}
          />
          <button
            type="button"
            onClick={() => goldRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); addFiles(e.dataTransfer.files); }}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2.5 text-left hover:border-brand hover:bg-brand-subtle/20"
          >
            <Upload className="h-4 w-4 text-ink-muted" />
            <span className="text-ink-muted">
              {golds.length ? "Add more files…" : "Choose gold files, or drop them here…"}
            </span>
          </button>

          {golds.length > 0 && (
            <ul className="mt-2 space-y-1">
              {golds.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center gap-2 rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs"
                >
                  <span className="flex-1 truncate text-ink">{f.name}</span>
                  <span className="text-ink-muted">{Math.round(f.size / 1024)} KB</span>
                  <button
                    onClick={() => setGolds(g => g.filter((_, j) => j !== i))}
                    className="text-ink-subtle hover:text-danger"
                    title="Remove"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="text-xs font-semibold text-ink">Matching source extract</span>
            <span className="text-[11px] text-ink-muted">optional</span>
          </div>
          <input
            ref={srcRef}
            type="file"
            accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt"
            className="hidden"
            onChange={e => setSource(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            onClick={() => srcRef.current?.click()}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2.5 text-left hover:border-brand hover:bg-brand-subtle/20"
          >
            <Upload className="h-4 w-4 text-ink-muted" />
            <span className={source ? "text-ink" : "text-ink-muted"}>
              {source ? source.name : "Choose the legacy extract this gold was built from…"}
            </span>
          </button>
          <p className="mt-1 text-[11px] text-ink-muted">
            One extract covers the whole batch — which is right for a fan-out, where a
            single legacy supplier file produced all six outputs. Without it we still
            learn constant defaults and the columns gold leaves blank. With it we can
            also work out which source column feeds each target field — that's inferred
            by overlapping the actual values, so it needs the source data.
          </p>
        </div>

        {single && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-xs font-semibold text-ink">Name</div>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Defaults to the file name"
                className="w-full rounded-lg border border-line px-3 py-2 text-sm"
              />
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-ink">Template</div>
              <select
                value={templateId}
                onChange={e => setTemplateId(e.target.value)}
                className="w-full rounded-lg border border-line px-3 py-2 text-sm"
              >
                <option value="">Detect from the file's headers</option>
                {templates.map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {golds.length > 1 && (
          <p className="text-[11px] text-ink-muted">
            Each file names itself and detects its own template. Upload one on its own if
            you need to override either.
          </p>
        )}

        {result && (
          <div className="rounded-lg border border-line">
            <div className="flex items-center gap-2 border-b border-line bg-canvas px-3 py-2 text-xs">
              {result.uploaded > 0 && <Pill tone="success">{result.uploaded} learned</Pill>}
              {result.unmatched > 0 && <Pill tone="warning">{result.unmatched} unmatched</Pill>}
              {result.failed > 0 && <Pill tone="danger">{result.failed} failed</Pill>}
            </div>
            <ul className="max-h-48 overflow-auto">
              {result.items.map((it: any, i) => (
                <li key={i} className="flex items-start gap-2 border-b border-line px-3 py-1.5 text-xs last:border-0">
                  {it.status === "learned" ? (
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
                  ) : (
                    <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-ink">{it.file_name ?? it.name}</div>
                    {it.status === "learned" ? (
                      <div className="text-ink-muted">
                        {it.target_object} · {it.defaults_learned} defaults ·{" "}
                        {it.suppressed_learned} blank-by-design ·{" "}
                        {it.mappings_learned} column maps
                      </div>
                    ) : (
                      <div className="text-warning">{it.note}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {err && (
          <div className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-xs text-danger">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
};

/** Gold that taught the tool before the library existed.
 *
 * The old conversion-side upload learned from the file and then deleted it, so the
 * rules are live and being applied but the artefact is gone. Hiding these would make
 * it look like nothing was ever taught for that object, which is worse than saying
 * plainly what we have and what we don't.
 */
const OrphanSection: React.FC<{ orphans: GoldOrphan[] }> = ({ orphans }) => (
  <Card className="mt-4 border-warning/30">
    <CardHeader
      title={
        <span className="inline-flex items-center gap-1.5">
          <CircleAlert className="h-4 w-4 text-warning" /> Learned before the library existed
        </span>
      }
      subtitle="These rules came from gold files uploaded on a conversion, back when the file itself wasn't kept. The learning is live and still applied on every generate — but there's no file to download or re-learn from. Re-upload the gold here to store it."
    />
    <CardBody className="p-0">
      <table className="w-full text-sm">
        <thead className="border-b border-line bg-canvas text-xs text-ink-muted">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Object</th>
            <th className="px-4 py-2 text-right font-medium">Rules in force</th>
            <th className="px-4 py-2 text-right font-medium">Defaults</th>
            <th className="px-4 py-2 text-right font-medium">Blank-by-design</th>
            <th className="px-4 py-2 text-right font-medium">Column maps</th>
            <th className="px-4 py-2 text-left font-medium">File</th>
          </tr>
        </thead>
        <tbody>
          {orphans.map(o => (
            <tr key={o.target_object} className="border-b border-line last:border-0">
              <td className="px-4 py-2.5 font-medium text-ink">{o.target_object}</td>
              <td className="px-4 py-2.5 text-right font-medium text-ink">{o.rules}</td>
              <td className="px-4 py-2.5 text-right text-ink-muted">{o.defaults || "—"}</td>
              <td className="px-4 py-2.5 text-right text-ink-muted">{o.suppressed || "—"}</td>
              <td className="px-4 py-2.5 text-right text-ink-muted">{o.mappings || "—"}</td>
              <td className="px-4 py-2.5">
                <Pill tone="warning">Not retained</Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </CardBody>
  </Card>
);

/** Correct a wrongly-detected template, or rename the file.
 *
 * Worth being explicit in the UI: changing the template is not a label change. The
 * rules this file taught were keyed to the object the old template belongs to, so
 * they've been applied to the wrong conversions. Saving re-learns the file against
 * the new template and re-keys everything.
 */
const EditModal: React.FC<{
  gold: GoldStandard | null;
  templates: FBDITemplate[];
  onClose: () => void;
  onSaved: () => void;
}> = ({ gold, templates, onClose, onSaved }) => {
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (gold) {
      setName(gold.name ?? "");
      setTemplateId(gold.template_id ?? "");
      setErr(null);
    }
  }, [gold]);

  if (!gold) return null;

  const changingTemplate = templateId && templateId !== (gold.template_id ?? "");
  const newTemplate = templates.find(t => t.id === templateId);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await GoldApi.patch(gold.id, {
        name: name !== gold.name ? name : undefined,
        template_id: changingTemplate ? templateId : undefined,
      });
      onSaved();
      onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Couldn't save that.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Edit gold standard"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={busy}>
            {changingTemplate ? "Save & re-learn" : "Save"}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <div>
          <div className="mb-1 text-xs font-semibold text-ink">Name</div>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            className="w-full rounded-lg border border-line px-3 py-2 text-sm"
          />
        </div>

        <div>
          <div className="mb-1 text-xs font-semibold text-ink">Template</div>
          <select
            value={templateId}
            onChange={e => setTemplateId(e.target.value)}
            className="w-full rounded-lg border border-line px-3 py-2 text-sm"
          >
            <option value="">— none —</option>
            {templates.map(t => (
              <option key={t.id} value={t.id}>
                {t.name}{t.business_object ? ` · ${t.business_object}` : ""}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-ink-muted">
            Detected from the file's headers
            {gold.match_confidence ? ` (${Math.round(gold.match_confidence * 100)}% match)` : ""}.
            Change it if that's wrong.
          </p>
        </div>

        {changingTemplate && (
          <div className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-xs text-ink">
            <div className="font-semibold text-warning">This re-learns the file.</div>
            <p className="mt-1 text-ink-muted">
              The rules this file taught were keyed to{" "}
              <span className="font-medium text-ink">{gold.target_object ?? "its old object"}</span>,
              so they've been applied to conversions of that object. Saving re-derives
              them against{" "}
              <span className="font-medium text-ink">
                {newTemplate?.business_object || newTemplate?.name || "the new template"}
              </span>{" "}
              and re-keys them. Rules the old object learned from this file are retired,
              unless another gold file still teaches it.
            </p>
          </div>
        )}

        {err && (
          <div className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-xs text-danger">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
};

const GoldStandardsPage: React.FC = () => {
  const [items, setItems] = useState<GoldStandard[]>([]);
  const [orphans, setOrphans] = useState<GoldOrphan[]>([]);
  const [summary, setSummary] = useState<{ gold_files: number; objects_covered: string[]; rules_from_gold: number } | null>(null);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [editing, setEditing] = useState<GoldStandard | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [g, t] = await Promise.all([GoldApi.list(), FbdiApi.list()]);
      setItems(g.items);
      setOrphans(g.orphans ?? []);
      setSummary(g.summary);
      setTemplates(t);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const grouped = useMemo(() => {
    const m = new Map<string, GoldStandard[]>();
    for (const g of items) {
      const k = g.target_object || "Unmatched";
      m.set(k, [...(m.get(k) ?? []), g]);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [items]);

  const onRelearn = async (g: GoldStandard) => {
    setBusyId(g.id);
    try {
      await GoldApi.relearn(g.id);
      await load();
    } finally {
      setBusyId(null);
    }
  };

  const onDelete = async (g: GoldStandard) => {
    if (!window.confirm(`Remove "${g.name}" from the gold standard library?`)) return;
    // Deleting the file and unlearning what it taught are different decisions. The
    // rules may have been reviewed, edited, or reinforced by other gold files, so
    // keeping them is the safe default — silently unlearning would change the output
    // of every future conversion of this object with no warning.
    const purge = window.confirm(
      `Also unlearn the rules it taught for ${g.target_object ?? "this object"}?\n\n` +
      `OK = unlearn them too.\n` +
      `Cancel = keep the rules (recommended — they may have been reviewed or reinforced by other gold files).`
    );
    setBusyId(g.id);
    try {
      await GoldApi.remove(g.id, purge);
      await load();
    } finally {
      setBusyId(null);
    }
  };

  if (loading && items.length === 0) return <PageLoader label="Loading gold standards…" />;

  return (
    <div className="p-6">
      <PageTitle
        title="Gold Standards"
        subtitle="Client-approved FBDI output, shared across every project. What's learned here is applied automatically whenever you generate that object."
        right={
          <Button onClick={() => setUploadOpen(true)}>
            <Upload className="mr-1 h-4 w-4" /> Add gold standard
          </Button>
        }
      />

      {summary && summary.gold_files > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Pill tone="brand">{summary.gold_files} gold files</Pill>
          <Pill tone="success">{summary.rules_from_gold} live rules</Pill>
          {summary.objects_covered.map(o => (
            <Pill key={o} tone="neutral">{o}</Pill>
          ))}
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          icon={<Award className="h-5 w-5" />}
          title="No gold standards yet"
          description="Upload an FBDI file the client has already signed off. The tool reads the defaults and the fields they deliberately leave blank, and reuses them on every conversion of that object — no project needed."
          action={
            <Button onClick={() => setUploadOpen(true)}>
              <Upload className="mr-1 h-4 w-4" /> Add gold standard
            </Button>
          }
        />
      ) : (
        <div className="space-y-4">
          {grouped.map(([object, rows]) => (
            <Card key={object}>
              <CardHeader
                title={object}
                subtitle={`${rows.length} file${rows.length === 1 ? "" : "s"}`}
              />
              <CardBody className="p-0">
                <table className="w-full text-sm">
                  <thead className="border-b border-line bg-canvas text-xs text-ink-muted">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">Name</th>
                      <th className="px-4 py-2 text-left font-medium">Template</th>
                      <th className="px-4 py-2 text-right font-medium">Rows</th>
                      <th className="px-4 py-2 text-right font-medium">Defaults</th>
                      <th className="px-4 py-2 text-right font-medium">Blank-by-design</th>
                      <th className="px-4 py-2 text-right font-medium">Column maps</th>
                      <th className="px-4 py-2 text-left font-medium">Status</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(g => {
                      const st = STATUS[g.status];
                      return (
                        <React.Fragment key={g.id}>
                          <tr className="border-b border-line last:border-0">
                            <td className="px-4 py-2.5">
                              <div className="font-medium text-ink">{g.name}</div>
                              <div className="text-xs text-ink-muted">{g.file_name}</div>
                            </td>
                            <td className="px-4 py-2.5 text-xs">
                              <button
                                onClick={() => setEditing(g)}
                                className="group inline-flex items-center gap-1 rounded px-1 py-0.5 text-ink-muted hover:bg-canvas hover:text-ink"
                                title="Detected from the file's headers — click to correct it"
                              >
                                {g.template_name ?? "Not matched"}
                                <Pencil className="h-3 w-3 opacity-0 transition group-hover:opacity-100" />
                              </button>
                            </td>
                            <td className="px-4 py-2.5 text-right text-ink-muted">
                              {formatNumber(g.rows)}
                            </td>
                            <td className="px-4 py-2.5 text-right font-medium text-ink">
                              {g.defaults_learned || "—"}
                            </td>
                            <td className="px-4 py-2.5 text-right font-medium text-ink">
                              {g.suppressed_learned || "—"}
                            </td>
                            <td className="px-4 py-2.5 text-right font-medium text-ink">
                              {g.mappings_learned || (
                                <span className="text-ink-muted" title="Add a source extract to learn these">
                                  —
                                </span>
                              )}
                            </td>
                            <td className="px-4 py-2.5">
                              <span className="inline-flex items-center gap-1">
                                {g.status === "learned" ? (
                                  <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                                ) : (
                                  <CircleAlert className="h-3.5 w-3.5 text-warning" />
                                )}
                                <Pill tone={st.tone}>{st.label}</Pill>
                              </span>
                            </td>
                            <td className="px-4 py-2.5">
                              <div className="flex items-center justify-end gap-1">
                                <Button
                                  variant="ghost"
                                  onClick={() => setEditing(g)}
                                  title="Rename, or correct the matched template"
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  onClick={() => void onRelearn(g)}
                                  loading={busyId === g.id}
                                  title="Re-derive rules from the stored file"
                                >
                                  <RefreshCw className="h-3.5 w-3.5" />
                                </Button>
                                <a
                                  href={GoldApi.downloadUrl(g.id)}
                                  className="rounded px-2 py-1 text-xs text-ink-muted hover:bg-canvas hover:text-ink"
                                >
                                  Download
                                </a>
                                <Button
                                  variant="ghost"
                                  onClick={() => void onDelete(g)}
                                  title="Remove from library"
                                >
                                  <Trash2 className="h-3.5 w-3.5 text-danger" />
                                </Button>
                              </div>
                            </td>
                          </tr>
                          {g.note && (
                            <tr className="border-b border-line last:border-0">
                              <td colSpan={8} className="bg-canvas px-4 py-2 text-xs text-ink-muted">
                                {g.note}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {orphans.length > 0 && <OrphanSection orphans={orphans} />}

      <EditModal
        gold={editing}
        templates={templates}
        onClose={() => setEditing(null)}
        onSaved={() => void load()}
      />

      <UploadModal
        open={uploadOpen}
        templates={templates}
        onClose={() => setUploadOpen(false)}
        onDone={() => void load()}
      />
    </div>
  );
};

export default GoldStandardsPage;
