import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Award, CheckCircle2, CircleAlert, RefreshCw, Trash2, Upload,
} from "lucide-react";
import { FbdiApi, GoldApi, type FBDITemplate, type GoldStandard } from "@/api";
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
  const [gold, setGold] = useState<File | null>(null);
  const [source, setSource] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const goldRef = useRef<HTMLInputElement>(null);
  const srcRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setGold(null); setSource(null); setName(""); setTemplateId(""); setErr(null);
    }
  }, [open]);

  const submit = async () => {
    if (!gold) return;
    setBusy(true);
    setErr(null);
    try {
      await GoldApi.upload(gold, {
        name: name || undefined,
        templateId: templateId || undefined,
        sourceFile: source || undefined,
      });
      onDone();
      onClose();
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
      title="Add a gold standard"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!gold}>
            Upload &amp; learn
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          Upload an FBDI output the client has already approved. The tool learns what
          it can from it and applies those rules to every conversion of that object —
          you don't need a project.
        </p>

        <div>
          <div className="mb-1 text-xs font-semibold text-ink">Approved FBDI output</div>
          <input
            ref={goldRef}
            type="file"
            accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt"
            className="hidden"
            onChange={e => setGold(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            onClick={() => goldRef.current?.click()}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2.5 text-left hover:border-brand hover:bg-brand-subtle/20"
          >
            <Upload className="h-4 w-4 text-ink-muted" />
            <span className={gold ? "text-ink" : "text-ink-muted"}>
              {gold ? gold.name : "Choose the gold file…"}
            </span>
          </button>
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
            Without it we still learn constant defaults and the columns gold leaves blank.
            With it we can also work out which source column feeds each target field —
            that's inferred by overlapping the actual values, so it needs the source data.
          </p>
        </div>

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
  const [summary, setSummary] = useState<{ gold_files: number; objects_covered: string[]; rules_from_gold: number } | null>(null);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [g, t] = await Promise.all([GoldApi.list(), FbdiApi.list()]);
      setItems(g.items);
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
                            <td className="px-4 py-2.5 text-xs text-ink-muted">
                              {g.template_name ?? "—"}
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
