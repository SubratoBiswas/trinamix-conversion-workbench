import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, BookOpen, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Clock,
  Download, EyeOff, FileSpreadsheet, Layers, Search, Sparkles, Trash2, TrendingUp,
  Upload, X, Zap,
} from "lucide-react";
import { ClientsApi, LearningApi, ProjectsApi } from "@/api";
import type { CatalogStatus, ClientSummary, LearnedObjectGroup, MappingImportResult } from "@/api";
import {
  Button, Card, CardBody, CardHeader, Modal, PageLoader, PageTitle, Pill, Spinner,
} from "@/components/ui/Primitives";
import { formatDate, cn } from "@/lib/utils";
import type { LearnedMapping, LearningStats, Project } from "@/types";

/**
 * Learning Center — organised by OBJECT, not by row.
 *
 * The old view was a flat registry of every rule ever captured. That's fine at 20
 * rows and useless at 978, which is where it lands the moment a gold file
 * contributes a few hundred "leave this column blank" rules. The question people
 * actually arrive with is "what do we know about Supplier?" — so that's what the
 * page answers first, and the individual rows are one click below.
 */

// The internal `kind` values, in the order a human wants to read them: what we
// map, what we default, what we deliberately leave alone.
const KIND_ORDER = [
  "column_mapping", "example_default", "crosswalk",
  "reference_standard", "suppress_field", "file_classification",
];

const KIND_META: Record<string, { label: string; tone: "brand" | "success" | "info" | "neutral" | "warning"; hint: string }> = {
  column_mapping: { label: "Column mappings", tone: "brand", hint: "Which source column feeds each FBDI field" },
  example_default: { label: "Default values", tone: "success", hint: "The value gold puts in this column every time" },
  crosswalk: { label: "Value crosswalks", tone: "info", hint: "Legacy value → Oracle code" },
  reference_standard: { label: "Reference standards", tone: "warning", hint: "Key-column rules that flow to every downstream FK" },
  suppress_field: { label: "Left blank on purpose", tone: "neutral", hint: "Gold deliberately leaves these empty — we keep them empty" },
  file_classification: { label: "File classification", tone: "neutral", hint: "How uploaded files are recognised" },
};

const kindMeta = (k: string) =>
  KIND_META[k] ?? { label: k.replace(/_/g, " "), tone: "neutral" as const, hint: "" };

/** Import a source→target mapping workbook.
 *
 * A gold file makes the tool INFER the crosswalk from data. A mapping workbook
 * STATES it — a consultant already did the mapping — so it's the strongest signal
 * we can take, and each row becomes a reusable column_mapping applied on every
 * future conversion of that object. Header detection is forgiving; source-on-left,
 * target-on-right is assumed only as a fallback.
 */
const MappingImportModal: React.FC<{
  open: boolean;
  objects: string[];
  onClose: () => void;
  onDone: () => void;
}> = ({ open, objects, onClose, onDone }) => {
  const [files, setFiles] = useState<File[]>([]);
  const [defaultObject, setDefaultObject] = useState("");
  const [sourceSystem, setSourceSystem] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<MappingImportResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) { setFiles([]); setDefaultObject(""); setSourceSystem(""); setErr(null); setResult(null); }
  }, [open]);

  const add = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles(prev => {
      const seen = new Set(prev.map(f => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter(f => !seen.has(`${f.name}:${f.size}`))];
    });
    setResult(null);
  };

  const submit = async () => {
    if (!files.length) return;
    setBusy(true); setErr(null);
    try {
      const r = await LearningApi.importMappings(files, {
        defaultObject: defaultObject || undefined,
        sourceSystem: sourceSystem || undefined,
      });
      setResult(r);
      onDone();
      const anyError = r.files.some(f => f.error || (f.unresolved_object?.length ?? 0) > 0);
      if (!anyError) onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Import failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Import mapping workbook"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>{result ? "Done" : "Cancel"}</Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!files.length}>
            Import{files.length > 1 ? ` (${files.length})` : ""}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          A spreadsheet that lists which source column feeds which FBDI field. The tool
          takes each row as a reusable mapping and applies it on every future conversion
          of that object. It reads the usual column headings — source/legacy field,
          target/FBDI field, object, source system — and assumes source-on-left,
          target-on-right if a heading is unusual.
        </p>

        <div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls"
            className="hidden"
            onChange={e => { add(e.target.files); e.target.value = ""; }}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); add(e.dataTransfer.files); }}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2.5 text-left hover:border-brand hover:bg-brand-subtle/20"
          >
            <Upload className="h-4 w-4 text-ink-muted" />
            <span className="text-ink-muted">
              {files.length ? "Add more files…" : "Choose mapping workbooks, or drop them here…"}
            </span>
          </button>
          {files.length > 0 && (
            <ul className="mt-2 space-y-1">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="flex items-center gap-2 rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs">
                  <FileSpreadsheet className="h-3.5 w-3.5 text-ink-muted" />
                  <span className="flex-1 truncate text-ink">{f.name}</span>
                  <button onClick={() => setFiles(g => g.filter((_, j) => j !== i))} className="text-ink-subtle hover:text-danger">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">
              Object if the file doesn't say <span className="font-normal text-ink-muted">optional</span>
            </div>
            <input
              list="known-objects"
              value={defaultObject}
              onChange={e => setDefaultObject(e.target.value)}
              placeholder="e.g. Supplier"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
            <datalist id="known-objects">
              {objects.map(o => <option key={o} value={o} />)}
            </datalist>
            <p className="mt-1 text-[11px] text-ink-muted">
              Used only for rows without their own object column. Rows are otherwise keyed
              by the object named in the file, or inferred from the FBDI field.
            </p>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">
              Source system <span className="font-normal text-ink-muted">optional</span>
            </div>
            <input
              value={sourceSystem}
              onChange={e => setSourceSystem(e.target.value)}
              placeholder="e.g. NetSuite"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
          </div>
        </div>

        {result && (
          <div className="rounded-lg border border-line">
            <div className="flex items-center gap-2 border-b border-line bg-canvas px-3 py-2 text-xs">
              <Pill tone="success">{result.imported} new</Pill>
              {result.updated > 0 && <Pill tone="info">{result.updated} updated</Pill>}
              {result.skipped > 0 && <Pill tone="warning">{result.skipped} skipped</Pill>}
            </div>
            <ul className="max-h-52 overflow-auto">
              {result.files.map((f, i) => (
                <li key={i} className="flex items-start gap-2 border-b border-line px-3 py-2 text-xs last:border-0">
                  {f.error ? (
                    <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
                  ) : (
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-ink">{f.file_name}</div>
                    {f.error ? (
                      <div className="text-danger">{f.error}</div>
                    ) : (
                      <div className="text-ink-muted">
                        {f.imported} new · {f.updated} updated · {f.skipped} skipped
                        {f.objects?.length ? ` · ${f.objects.join(", ")}` : ""}
                        {f.columns_detected?.source && (
                          <span className="text-ink-subtle">
                            {" "}· read {f.columns_detected.source} → {f.columns_detected.target}
                          </span>
                        )}
                        {(f.unresolved_object?.length ?? 0) > 0 && (
                          <div className="mt-0.5 text-warning">
                            {f.unresolved_object!.length} row(s) had no object and were skipped —
                            set a default object above and re-import.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {err && (
          <div className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-xs text-danger">{err}</div>
        )}
      </div>
    </Modal>
  );
};

export const LearningCenterPage: React.FC = () => {
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [groups, setGroups] = useState<LearnedObjectGroup[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [catalog, setCatalog] = useState<CatalogStatus | null>(null);
  const [openObject, setOpenObject] = useState<string | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [knownObjects, setKnownObjects] = useState<string[]>([]);
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [clientFilter, setClientFilter] = useState<string>("");  // "" = all, "global", or a client id
  const [targetFilter, setTargetFilter] = useState<string>("");  // "" = all target objects, else an object name

  const refresh = async (pid?: string, cf: string = clientFilter) => {
    const params = pid ? { project_id: pid } : undefined;
    const [s, g] = await Promise.all([
      LearningApi.stats(params),
      LearningApi.byObject(cf || undefined),
    ]);
    setStats(s);
    setGroups(g.objects);
  };

  useEffect(() => {
    void refresh();
    ProjectsApi.list().then(setProjects).catch(() => {});
    ClientsApi.list().then(r => setClients(r.clients)).catch(() => {});
    LearningApi.catalogStatus().then(setCatalog).catch(() => setCatalog(null));
    LearningApi.knownObjects().then(r => setKnownObjects(r.objects)).catch(() => {});
  }, []);

  if (!stats || !groups) return <PageLoader label="Loading what the tool has learned…" />;

  const isEmpty = stats.total === 0;

  return (
    <>
      <PageTitle
        title="Learning Center"
        subtitle={
          isEmpty
            ? "Everything the tool has learned from your gold files, corrections and rules — reused automatically on future conversions."
            : `${stats.total} rules across ${groups.length} object${groups.length === 1 ? "" : "s"}. Pick an object to see what the tool knows about it.`
        }
        right={
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              <Upload className="mr-1 h-4 w-4" /> Import mappings
            </Button>
            {clients.length > 0 && (
              <select
                value={clientFilter}
                onChange={e => {
                  const cf = e.target.value;
                  setClientFilter(cf);
                  setStats(null); setGroups(null);
                  void refresh(selectedProjectId || undefined, cf);
                }}
                title="Filter by client — client-scoped learnings plus anything global"
                className="h-9 rounded-md border border-line bg-white pl-3 pr-8 text-sm text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              >
                <option value="">All clients</option>
                <option value="global">Global only</option>
                {clients.map(c => (
                  <option key={c.id} value={c.id}>{c.name}{c.is_default ? " (default)" : ""}</option>
                ))}
              </select>
            )}
            {groups && groups.length > 0 && (
              <select
                value={targetFilter}
                onChange={e => setTargetFilter(e.target.value)}
                title="Filter by target FBDI object"
                className="h-9 rounded-md border border-line bg-white pl-3 pr-8 text-sm text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              >
                <option value="">All target FBDI</option>
                {groups.map(g => (
                  <option key={g.target_object} value={g.target_object}>{g.target_object}</option>
                ))}
              </select>
            )}
            {projects.length > 0 && (
              <select
                value={selectedProjectId}
                onChange={e => {
                  setSelectedProjectId(e.target.value);
                  setStats(null); setGroups(null);
                  void refresh(e.target.value || undefined);
                }}
                className="h-9 rounded-md border border-line bg-white pl-3 pr-8 text-sm text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              >
                <option value="">All engagements</option>
                {projects.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.client ? ` · ${p.client}` : ""}
                  </option>
                ))}
              </select>
            )}
            {!isEmpty && (
              <Button
                variant="secondary"
                loading={backfilling}
                onClick={async () => {
                  setBackfilling(true);
                  try {
                    const r = await LearningApi.backfillProjects();
                    alert(`Backfill complete: ${r.updated} updated, ${r.skipped_no_match} skipped`);
                    await refresh(selectedProjectId || undefined);
                  } finally {
                    setBackfilling(false);
                  }
                }}
              >
                Fix project links
              </Button>
            )}
          </div>
        }
      />

      {isEmpty ? <EmptyHero /> : <KpiStrip stats={stats} />}

      {!isEmpty && (
        <Card className="mt-5">
          <CardHeader
            title={
              <span className="inline-flex items-center gap-1.5">
                <Layers className="h-4 w-4 text-brand" /> What the tool knows, by object
              </span>
            }
            subtitle="Each object carries its own rules. They're applied automatically whenever you generate that object — no re-teaching."
          />
          <CardBody className="p-0">
            {groups.filter(g => !targetFilter || g.target_object === targetFilter).map(g => (
              <ObjectRow
                key={g.target_object}
                group={g}
                clientFilter={clientFilter}
                open={openObject === g.target_object}
                onToggle={() =>
                  setOpenObject(openObject === g.target_object ? null : g.target_object)
                }
                onChanged={() => void refresh(selectedProjectId || undefined)}
              />
            ))}
          </CardBody>
        </Card>
      )}

      {catalog && catalog.total > 0 && <CatalogCard catalog={catalog} />}

      <MappingImportModal
        open={importOpen}
        objects={
          knownObjects.length
            ? knownObjects
            : groups.map(g => g.target_object).filter(o => o !== "Not tied to an object")
        }
        onClose={() => setImportOpen(false)}
        onDone={() => void refresh(selectedProjectId || undefined)}
      />
    </>
  );
};

// ---------------------------------------------------------------- object row

const ObjectRow: React.FC<{
  group: LearnedObjectGroup;
  clientFilter: string;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}> = ({ group, clientFilter, open, onToggle, onChanged }) => {
  const kinds = useMemo(
    () =>
      [...group.kinds].sort(
        (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)
      ),
    [group.kinds]
  );

  return (
    <div className="border-b border-line last:border-0">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-canvas"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-ink-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-ink-muted" />
        )}
        <div className="min-w-[190px]">
          <div className="text-sm font-semibold text-ink">{group.target_object}</div>
          <div className="text-[11px] text-ink-muted">
            {group.total} rule{group.total === 1 ? "" : "s"}
            {group.last_captured && <> · updated {formatDate(group.last_captured)}</>}
          </div>
        </div>
        <div className="flex flex-1 flex-wrap items-center gap-1.5">
          {kinds.map(k => {
            const m = kindMeta(k.kind);
            return (
              <Pill key={k.kind} tone={m.tone} className="whitespace-nowrap">
                {k.count} {m.label.toLowerCase()}
              </Pill>
            );
          })}
        </div>
        {group.sources.length > 0 && (
          <span className="hidden text-[11px] text-ink-muted lg:inline">
            from {group.sources.slice(0, 2).join(", ")}
            {group.sources.length > 2 && ` +${group.sources.length - 2}`}
          </span>
        )}
      </button>

      {open && <ObjectDetail group={group} clientFilter={clientFilter} onChanged={onChanged} />}
    </div>
  );
};

// ------------------------------------------------------------- object detail

const ObjectDetail: React.FC<{ group: LearnedObjectGroup; clientFilter: string; onChanged: () => void }> = ({
  group, clientFilter, onChanged,
}) => {
  const [rows, setRows] = useState<LearnedMapping[] | null>(null);
  const [kind, setKind] = useState<string>(() => {
    // Open on the most useful kind, never on the 500-row suppression list.
    const useful = [...group.kinds]
      .filter(k => k.kind !== "suppress_field")
      .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
    return (useful[0] ?? group.kinds[0])?.kind ?? "column_mapping";
  });
  const [q, setQ] = useState("");

  const objParam =
    group.target_object === "Not tied to an object" ? undefined : group.target_object;

  const load = async () => {
    setRows(null);
    const data = await LearningApi.list({
      target_object: objParam,
      kind,
      client_id: clientFilter || undefined,
      q: q.trim() || undefined,
    });
    // The API can't filter the catch-all bucket server-side (its rows have no
    // object at all), so narrow it here.
    setRows(objParam ? data : data.filter(r => !r.target_object));
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.target_object, kind]);

  const meta = kindMeta(kind);
  const kinds = [...group.kinds].sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)
  );

  const exportCsv = () => {
    if (!rows?.length) return;
    const head = "field,original_value,resolved_value,kind,captured_from,captured_at";
    const body = rows.map(r =>
      [r.target_field ?? "", JSON.stringify(r.original_value ?? ""),
       JSON.stringify(r.resolved_value ?? ""), r.kind,
       r.captured_from ?? "", r.captured_at].join(",")
    );
    const url = URL.createObjectURL(
      new Blob([[head, ...body].join("\n")], { type: "text/csv" })
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `learned_${group.target_object.replace(/\s+/g, "_")}_${kind}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="border-t border-line bg-canvas px-5 py-4">
      {/* kind switcher */}
      <div className="flex flex-wrap items-center gap-1.5">
        {kinds.map(k => {
          const m = kindMeta(k.kind);
          const active = k.kind === kind;
          return (
            <button
              key={k.kind}
              onClick={() => setKind(k.kind)}
              title={m.hint}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs transition",
                active
                  ? "border-brand bg-brand text-white"
                  : "border-line bg-white text-ink-muted hover:border-brand hover:text-ink"
              )}
            >
              {m.label}
              <span className={cn("ml-1.5 tabular-nums", active ? "text-white/80" : "text-ink-subtle")}>
                {k.count}
              </span>
            </button>
          );
        })}
      </div>

      {meta.hint && <p className="mt-2 text-[11.5px] text-ink-muted">{meta.hint}</p>}

      <div className="mt-3 flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle" />
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === "Enter" && void load()}
            placeholder={`Search ${meta.label.toLowerCase()} in ${group.target_object}…`}
            className="w-full rounded-md border border-line bg-white py-1.5 pl-8 pr-3 text-xs"
          />
        </div>
        <Button variant="ghost" onClick={() => void load()}>Search</Button>
        <Button variant="ghost" onClick={exportCsv} disabled={!rows?.length}>
          <Download className="mr-1 h-3.5 w-3.5" /> CSV
        </Button>
      </div>

      {rows === null ? (
        <div className="flex items-center gap-2 py-6 text-xs text-ink-muted">
          <Spinner /> Loading…
        </div>
      ) : rows.length === 0 ? (
        <p className="py-6 text-center text-xs text-ink-muted">Nothing here.</p>
      ) : kind === "suppress_field" ? (
        <SuppressedList rows={rows} onForget={async id => {
          await LearningApi.delete(id); await load(); onChanged();
        }} />
      ) : (
        <div className="mt-3 max-h-96 overflow-auto rounded-md border border-line bg-white">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-canvas text-left text-[10px] uppercase tracking-wider text-ink-muted">
              <tr>
                <th className="px-3 py-1.5">FBDI field</th>
                <th className="px-3 py-1.5">{kind === "column_mapping" ? "Source column" : "From"}</th>
                <th className="w-6" />
                <th className="px-3 py-1.5">{kind === "example_default" ? "Value written" : "To"}</th>
                {/* Two learnings can name the same FBDI field and differ only by the
                    legacy system they came from (NetSuite Item vs SyteLine Item) or by
                    the interface sheets they apply to. Both were stored and enforced
                    but absent from the payload, so the rows looked identical and a
                    sheet exclusion could not be confirmed after it was set. */}
                <th className="px-3 py-1.5">Scope</th>
                <th className="px-3 py-1.5">Learned from</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-t border-line/60">
                  <td className="px-3 py-1.5 font-medium text-ink">{r.target_field || "—"}</td>
                  <td className="px-3 py-1.5 font-mono text-ink-muted">{r.original_value || "—"}</td>
                  <td className="text-ink-subtle">
                    <ArrowRight className="h-3 w-3" />
                  </td>
                  <td className="px-3 py-1.5 font-mono text-success">{r.resolved_value || "—"}</td>
                  <td className="px-3 py-1.5 text-[11px] text-ink-muted">
                    <div className="flex flex-wrap items-center gap-1">
                      {r.source_erp
                        ? <Pill tone="info">{r.source_erp}</Pill>
                        : <span className="text-ink-subtle">any source</span>}
                      {(r.sheets?.length ?? 0) > 0 && (
                        <span title={r.sheets!.join(", ")}>
                          <Pill tone="neutral">
                            only {r.sheets!.length} sheet{r.sheets!.length === 1 ? "" : "s"}
                          </Pill>
                        </span>
                      )}
                      {(r.exclude_sheets?.length ?? 0) > 0 && (
                        <span title={r.exclude_sheets!.join(", ")}>
                          <Pill tone="warning">
                            not on {r.exclude_sheets!.length} sheet{r.exclude_sheets!.length === 1 ? "" : "s"}
                          </Pill>
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-[11px] text-ink-muted">
                    {r.captured_from || formatDate(r.captured_at)}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      onClick={async () => {
                        await LearningApi.delete(r.id);
                        await load();
                        onChanged();
                      }}
                      className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
                      title="Forget this rule"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

/** Suppressions are high-volume and low-information per row — show them as chips,
 *  not as 500 table rows nobody will read. */
const SuppressedList: React.FC<{
  rows: LearnedMapping[];
  onForget: (id: string) => void | Promise<void>;
}> = ({ rows, onForget }) => {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : rows.slice(0, 40);
  return (
    <div className="mt-3 rounded-md border border-line bg-white p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] text-ink-muted">
        <EyeOff className="h-3.5 w-3.5" />
        These columns exist in the template but gold leaves them empty, so the tool
        keeps them empty — this is what stops the AI over-filling the file.
      </div>
      <div className="flex flex-wrap gap-1.5">
        {shown.map(r => (
          <span
            key={r.id}
            className="group inline-flex items-center gap-1 rounded border border-line bg-canvas px-1.5 py-0.5 font-mono text-[11px] text-ink-muted"
          >
            {r.target_field}
            <button
              onClick={() => void onForget(r.id)}
              className="opacity-0 transition group-hover:opacity-100 hover:text-danger"
              title="Stop suppressing this field"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      {rows.length > 40 && (
        <button
          onClick={() => setShowAll(s => !s)}
          className="mt-2 text-[11px] font-medium text-brand hover:underline"
        >
          {showAll ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
};

// ------------------------------------------------------------------- catalog

const CatalogCard: React.FC<{ catalog: CatalogStatus }> = ({ catalog }) => {
  const [open, setOpen] = useState(false);
  return (
    <Card className="mt-5">
      <CardHeader
        title="Mapping knowledge base — metadata catalog"
        subtitle={`${catalog.total} standard source→FBDI column mappings seeded from public schemas (NetSuite, Infor SyteLine, Salesforce). Applied automatically when a matching source file is converted.`}
        actions={
          <Button variant="ghost" onClick={() => setOpen(o => !o)}>
            {open ? "Hide" : "View"}
          </Button>
        }
      />
      {open && (
        <CardBody>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                By source system
              </div>
              <div className="flex flex-wrap gap-1.5">
                {catalog.by_source_system.map(s => (
                  <Pill key={s.source_system} tone="brand">{s.source_system}: {s.count}</Pill>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                By target object
              </div>
              <div className="flex flex-wrap gap-1.5">
                {catalog.by_target_object.map(o => (
                  <Pill key={o.target_object} tone="neutral">{o.target_object}: {o.count}</Pill>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-3 max-h-64 overflow-auto rounded-md border border-line">
            <table className="w-full text-[11.5px]">
              <thead className="sticky top-0 bg-canvas text-left text-[10px] uppercase tracking-wider text-ink-muted">
                <tr>
                  <th className="px-2 py-1">Source</th>
                  <th className="px-2 py-1">Source field</th>
                  <th className="px-2 py-1">Object</th>
                  <th className="px-2 py-1">FBDI column</th>
                </tr>
              </thead>
              <tbody>
                {catalog.rows.map((r, i) => (
                  <tr key={i} className="border-t border-line/60">
                    <td className="px-2 py-1 text-ink-muted">{r.source_system}</td>
                    <td className="px-2 py-1 font-mono text-ink">{r.source_field}</td>
                    <td className="px-2 py-1 text-ink-muted">{r.target_object}</td>
                    <td className="px-2 py-1 font-medium text-ink">{r.fbdi_column}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardBody>
      )}
    </Card>
  );
};

// --------------------------------------------------------------------- chrome

const EmptyHero: React.FC = () => (
  <div className="rounded-lg border border-line bg-white px-6 py-12 text-center">
    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-brand-subtle text-brand">
      <BookOpen className="h-5 w-5" />
    </div>
    <div className="mt-4 text-base font-semibold text-ink">Nothing learned yet</div>
    <p className="mx-auto mt-2 max-w-lg text-sm text-ink-muted">
      Upload a gold standard, or approve a mapping in Mapping Review. Whatever you
      teach the tool about an object is reused on every future conversion of it.
    </p>
  </div>
);

const KpiStrip: React.FC<{ stats: LearningStats }> = ({ stats }) => {
  const noAiPct = stats.total ? Math.round((stats.reusable_no_ai / stats.total) * 100) : 0;
  return (
    <div className="rounded-lg border border-brand/20 bg-gradient-to-br from-brand-subtle/50 to-white p-4">
      <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
        <TrendingUp className="h-3.5 w-3.5" /> What the tool has learned
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiTile icon={Sparkles} label="Rules captured" value={stats.total} tone="text-brand-dark" />
        <KpiTile icon={Layers} label="Objects covered" value={stats.objects_covered} tone="text-info" />
        <KpiTile
          icon={Zap}
          label="Resolve without AI"
          value={stats.reusable_no_ai}
          sub={stats.total ? `${noAiPct}% of all rules` : undefined}
          tone="text-success"
        />
        <KpiTile
          icon={Clock}
          label="Times auto-applied"
          value={stats.times_applied}
          sub="to conversion fields"
          tone="text-warning"
        />
      </div>
    </div>
  );
};

const KpiTile: React.FC<{
  icon: React.ElementType; label: string; value: React.ReactNode; tone: string; sub?: string;
}> = ({ icon: Icon, label, value, tone, sub }) => (
  <div className="rounded-md border border-line bg-white px-4 py-3">
    <div className="flex items-center gap-1.5 text-ink-muted">
      <Icon className={cn("h-3.5 w-3.5", tone)} />
      <span className="text-[10.5px] uppercase tracking-wider">{label}</span>
    </div>
    <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
    {sub && <div className="mt-0.5 text-[10.5px] text-ink-muted">{sub}</div>}
  </div>
);

export default LearningCenterPage;
