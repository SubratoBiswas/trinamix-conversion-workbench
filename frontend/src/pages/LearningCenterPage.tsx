import React, { useEffect, useMemo, useState } from "react";
import {
  ArrowRight, BookOpen, ChevronDown, ChevronRight, Clock, Download, EyeOff,
  Layers, Search, Sparkles, Trash2, TrendingUp, Zap,
} from "lucide-react";
import { LearningApi, ProjectsApi } from "@/api";
import type { CatalogStatus, LearnedObjectGroup } from "@/api";
import {
  Button, Card, CardBody, CardHeader, PageLoader, PageTitle, Pill, Spinner,
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

export const LearningCenterPage: React.FC = () => {
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [groups, setGroups] = useState<LearnedObjectGroup[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [catalog, setCatalog] = useState<CatalogStatus | null>(null);
  const [openObject, setOpenObject] = useState<string | null>(null);
  const [backfilling, setBackfilling] = useState(false);

  const refresh = async (pid?: string) => {
    const params = pid ? { project_id: pid } : undefined;
    const [s, g] = await Promise.all([
      LearningApi.stats(params),
      LearningApi.byObject(),
    ]);
    setStats(s);
    setGroups(g.objects);
  };

  useEffect(() => {
    void refresh();
    ProjectsApi.list().then(setProjects).catch(() => {});
    LearningApi.catalogStatus().then(setCatalog).catch(() => setCatalog(null));
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
            {groups.map(g => (
              <ObjectRow
                key={g.target_object}
                group={g}
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
    </>
  );
};

// ---------------------------------------------------------------- object row

const ObjectRow: React.FC<{
  group: LearnedObjectGroup;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}> = ({ group, open, onToggle, onChanged }) => {
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

      {open && <ObjectDetail group={group} onChanged={onChanged} />}
    </div>
  );
};

// ------------------------------------------------------------- object detail

const ObjectDetail: React.FC<{ group: LearnedObjectGroup; onChanged: () => void }> = ({
  group, onChanged,
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

const KpiStrip: React.FC<{ stats: LearningStats }> = ({ stats }) => (
  <div className="rounded-lg border border-brand/20 bg-gradient-to-br from-brand-subtle/50 to-white p-4">
    <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
      <TrendingUp className="h-3.5 w-3.5" /> Feedback loop impact
    </div>
    <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
      <KpiTile icon={Sparkles} label="Rules captured" value={stats.total} tone="text-brand-dark" />
      <KpiTile icon={TrendingUp} label="Avg confidence boost"
        value={`+${Math.round((stats.avg_confidence_boost || 0) * 100)}%`} tone="text-success" />
      <KpiTile icon={Zap} label="Records auto-fixed" value={stats.records_auto_fixed} tone="text-info" />
      <KpiTile icon={Clock} label="Analyst time saved"
        value={`~${stats.analyst_minutes_saved}m`} tone="text-warning" />
    </div>
  </div>
);

const KpiTile: React.FC<{
  icon: React.ElementType; label: string; value: React.ReactNode; tone: string;
}> = ({ icon: Icon, label, value, tone }) => (
  <div className="rounded-md border border-line bg-white px-4 py-3">
    <div className="flex items-center gap-1.5 text-ink-muted">
      <Icon className={cn("h-3.5 w-3.5", tone)} />
      <span className="text-[10.5px] uppercase tracking-wider">{label}</span>
    </div>
    <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
  </div>
);

export default LearningCenterPage;
