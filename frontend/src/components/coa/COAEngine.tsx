import React, { useEffect, useMemo, useState } from "react";
import {
  Layers, Lock, Unlock, Plus, Trash2, AlertTriangle, CheckCircle2,
  Calculator, Upload, Loader2, RefreshCw, ChevronDown, ChevronRight,
  Edit2, X, Sparkles, ListChecks,
} from "lucide-react";
import { COAApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, Pill,
} from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  COAComposeResult, COACrosswalk, COASegment, COAStructure, DatasetDetail,
} from "@/types";

/**
 * COA Engine — embedded on ConversionDetailPage for GL / Chart-of-
 * Accounts conversions.
 *
 * Surface:
 *   • Structure header — name, separator, target ledger, lock toggle
 *   • Segment editor — N segments with derivation (constant /
 *     source_column / crosswalk / computed / conditional)
 *   • Per-segment crosswalk drawer — list, single add, bulk upload
 *   • "Run composition" — dry-run preview of N rows + coverage stats
 */

const DERIVATION_KINDS = [
  "constant", "source_column", "crosswalk", "computed", "conditional",
] as const;

export const COAEngine: React.FC<{
  conversionId: number;
  dataset: DatasetDetail | null;
}> = ({ conversionId, dataset }) => {
  const [structure, setStructure] = useState<COAStructure | null | undefined>(undefined);
  const [seeding, setSeeding] = useState(false);
  const [compose, setCompose] = useState<COAComposeResult | null>(null);
  const [composing, setComposing] = useState(false);
  const [composeError, setComposeError] = useState<string | null>(null);
  const [openSegment, setOpenSegment] = useState<COASegment | null>(null);

  const reload = async () => {
    try {
      const s = await COAApi.structure(conversionId);
      setStructure(s);
    } catch {
      setStructure(null);
    }
  };

  useEffect(() => { reload(); }, [conversionId]);

  const onSeed = async () => {
    setSeeding(true);
    try {
      const s = await COAApi.seed(conversionId);
      setStructure(s);
    } finally {
      setSeeding(false);
    }
  };

  const runCompose = async () => {
    setComposing(true);
    setComposeError(null);
    try {
      const result = await COAApi.compose(conversionId, 50);
      setCompose(result);
    } catch (e: any) {
      setComposeError(e?.response?.data?.detail || "Composition failed");
    } finally {
      setComposing(false);
    }
  };

  const onToggleLock = async () => {
    if (!structure) return;
    const updated = await COAApi.updateStructure(structure.id, { locked: !structure.locked });
    setStructure(updated);
  };

  if (structure === undefined) {
    return (
      <Card className="mt-4">
        <CardHeader title="COA Engine" subtitle="Loading…" />
        <CardBody><Loader2 className="h-4 w-4 animate-spin text-ink-muted" /></CardBody>
      </Card>
    );
  }

  if (!structure) {
    return (
      <Card className="mt-4">
        <CardHeader
          title={<span className="inline-flex items-center gap-1.5"><Layers className="h-4 w-4 text-brand" />COA Engine</span>}
          subtitle="Chart-of-Accounts multi-segment composition — seed the canonical 5-segment template to start."
        />
        <CardBody>
          <EmptyState
            icon={<Layers className="h-5 w-5" />}
            title="No COA structure yet"
            description="Seed a canonical Fusion 5-segment template (Company-CostCenter-NaturalAccount-SubAccount-Product), then bulk-upload your crosswalk per segment."
            action={<Button onClick={onSeed} loading={seeding}><Plus className="h-4 w-4" /> Seed canonical structure</Button>}
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <>
      <Card className="mt-4">
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <Layers className="h-4 w-4 text-brand" />
              COA Engine · {structure.name}
            </span>
          }
          subtitle={
            <span className="text-xs text-ink-muted">
              {structure.segments.length} segment{structure.segments.length === 1 ? "" : "s"} ·
              separator "<span className="font-mono">{structure.separator}</span>" ·
              ledger "{structure.target_ledger || "—"}"
            </span>
          }
          actions={
            <div className="flex items-center gap-2">
              <button
                onClick={onToggleLock}
                className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium hover:border-brand hover:text-brand-dark"
              >
                {structure.locked ? <Lock className="h-3 w-3 text-danger" /> : <Unlock className="h-3 w-3 text-ink-muted" />}
                {structure.locked ? "Unlock" : "Lock"}
              </button>
              <Button onClick={runCompose} loading={composing} className="!h-8 !text-xs">
                <Calculator className="h-3.5 w-3.5" /> Compose preview
              </Button>
            </div>
          }
        />
        <CardBody>
          <SegmentTable
            structure={structure}
            onEdit={setOpenSegment}
            onChange={reload}
            datasetColumns={(dataset?.columns || []).map((c) => c.column_name)}
          />
          <ComposedAccountExample structure={structure} compose={compose} />
          {composeError && (
            <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
              <AlertTriangle className="mr-1 inline h-3 w-3" /> {composeError}
            </div>
          )}
        </CardBody>
      </Card>

      {compose && <CoveragePanel compose={compose} />}

      {openSegment && (
        <SegmentCrosswalkDrawer
          segment={openSegment}
          onClose={() => setOpenSegment(null)}
          onChanged={() => { setOpenSegment(null); }}
        />
      )}
    </>
  );
};

const ComposedAccountExample: React.FC<{
  structure: COAStructure; compose: COAComposeResult | null;
}> = ({ structure, compose }) => {
  // Synthesise a composed example with placeholders so users always see
  // what an account string will look like, even before they compose.
  const placeholder = structure.segments
    .map((s) => "X".repeat(s.length))
    .join(structure.separator);
  return (
    <div className="mt-3 rounded-md border border-line bg-canvas px-3 py-2">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
        Account-code shape
      </div>
      <div className="mt-1 font-mono text-base font-semibold tabular-nums text-ink">
        {compose?.sample_rows[0]?.composed_account || placeholder}
      </div>
      {compose && (
        <div className="mt-1 text-[10.5px] text-ink-muted">
          first composed sample (row {compose.sample_rows[0]?.source_index + 1 || 0})
        </div>
      )}
    </div>
  );
};

const SegmentTable: React.FC<{
  structure: COAStructure;
  onEdit: (s: COASegment) => void;
  onChange: () => void;
  datasetColumns: string[];
}> = ({ structure, onEdit, onChange, datasetColumns }) => {
  const [adding, setAdding] = useState(false);
  return (
    <div>
      <table className="table-shell !text-[12px]">
        <thead>
          <tr>
            <th className="!w-12">Pos</th>
            <th>Segment</th>
            <th>Length</th>
            <th>Derivation</th>
            <th>Source column</th>
            <th>Default</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {structure.segments.map((s) => {
            const cfg = s.derivation_config || {};
            return (
              <tr key={s.id}>
                <td className="font-mono text-ink-muted">{s.position}</td>
                <td>
                  <span className="font-medium text-ink">{s.name}</span>
                  {s.description && (
                    <div className="text-[11px] text-ink-muted">{s.description}</div>
                  )}
                </td>
                <td className="font-mono text-ink-muted">{s.length}</td>
                <td>
                  <Pill tone="brand" className="!text-[10px]">
                    {s.derivation_kind}
                  </Pill>
                </td>
                <td className="font-mono text-[11px] text-ink-muted">{cfg.column || (cfg.value ? `="${cfg.value}"` : "—")}</td>
                <td className="font-mono text-[11px] text-ink-muted">{s.default_value || "—"}</td>
                <td className="text-right">
                  <div className="inline-flex items-center gap-1">
                    {s.derivation_kind === "crosswalk" && (
                      <button
                        onClick={() => onEdit(s)}
                        disabled={structure.locked}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[10.5px] font-medium text-brand-dark hover:border-brand disabled:opacity-50"
                      >
                        <ListChecks className="h-3 w-3" /> Crosswalk
                      </button>
                    )}
                    <EditSegmentInline
                      seg={s}
                      datasetColumns={datasetColumns}
                      locked={structure.locked}
                      onSaved={onChange}
                    />
                    <button
                      onClick={async () => {
                        if (!confirm(`Delete segment "${s.name}"?`)) return;
                        await COAApi.removeSegment(s.id);
                        onChange();
                      }}
                      disabled={structure.locked}
                      className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button
        onClick={() => setAdding(true)}
        disabled={structure.locked}
        className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline disabled:opacity-50"
      >
        <Plus className="h-3 w-3" /> Add segment
      </button>
      {adding && (
        <AddSegmentModal
          structureId={structure.id}
          datasetColumns={datasetColumns}
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); onChange(); }}
        />
      )}
    </div>
  );
};

const EditSegmentInline: React.FC<{
  seg: COASegment;
  datasetColumns: string[];
  locked: boolean;
  onSaved: () => void;
}> = ({ seg, datasetColumns, locked, onSaved }) => {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        disabled={locked}
        className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-brand-dark disabled:opacity-50"
      >
        <Edit2 className="h-3.5 w-3.5" />
      </button>
      {open && (
        <EditSegmentModal
          seg={seg}
          datasetColumns={datasetColumns}
          onClose={() => setOpen(false)}
          onSaved={() => { setOpen(false); onSaved(); }}
        />
      )}
    </>
  );
};

const AddSegmentModal: React.FC<{
  structureId: number;
  datasetColumns: string[];
  onClose: () => void;
  onSaved: () => void;
}> = ({ structureId, datasetColumns, onClose, onSaved }) => {
  const [name, setName] = useState("");
  const [length, setLength] = useState(4);
  const [kind, setKind] = useState<typeof DERIVATION_KINDS[number]>("source_column");
  const [column, setColumn] = useState(datasetColumns[0] || "");
  const [constant, setConstant] = useState("");
  const [defaultValue, setDefaultValue] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title="Add COA segment" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button loading={busy} disabled={!name.trim()} onClick={async () => {
        setBusy(true);
        try {
          await COAApi.addSegment(structureId, {
            name, length, derivation_kind: kind,
            derivation_config: kind === "constant"
              ? { value: constant }
              : { column },
            default_value: defaultValue || undefined,
          });
          onSaved();
        } finally { setBusy(false); }
      }}>Add</Button>
    </>}>
      <SegmentForm
        name={name} setName={setName}
        length={length} setLength={setLength}
        kind={kind} setKind={setKind}
        column={column} setColumn={setColumn}
        constant={constant} setConstant={setConstant}
        defaultValue={defaultValue} setDefaultValue={setDefaultValue}
        datasetColumns={datasetColumns}
      />
    </Modal>
  );
};

const EditSegmentModal: React.FC<{
  seg: COASegment;
  datasetColumns: string[];
  onClose: () => void;
  onSaved: () => void;
}> = ({ seg, datasetColumns, onClose, onSaved }) => {
  const cfg = seg.derivation_config || {};
  const [name, setName] = useState(seg.name);
  const [length, setLength] = useState(seg.length);
  const [kind, setKind] = useState(seg.derivation_kind as typeof DERIVATION_KINDS[number]);
  const [column, setColumn] = useState((cfg.column as string) || datasetColumns[0] || "");
  const [constant, setConstant] = useState((cfg.value as string) || "");
  const [defaultValue, setDefaultValue] = useState(seg.default_value || "");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title={`Edit segment · ${seg.name}`} footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button loading={busy} onClick={async () => {
        setBusy(true);
        try {
          await COAApi.updateSegment(seg.id, {
            name, length, derivation_kind: kind,
            derivation_config: kind === "constant"
              ? { value: constant }
              : { column },
            default_value: defaultValue || null,
          });
          onSaved();
        } finally { setBusy(false); }
      }}>Save</Button>
    </>}>
      <SegmentForm
        name={name} setName={setName}
        length={length} setLength={setLength}
        kind={kind} setKind={setKind}
        column={column} setColumn={setColumn}
        constant={constant} setConstant={setConstant}
        defaultValue={defaultValue} setDefaultValue={setDefaultValue}
        datasetColumns={datasetColumns}
      />
    </Modal>
  );
};

const SegmentForm: React.FC<{
  name: string; setName: (s: string) => void;
  length: number; setLength: (n: number) => void;
  kind: typeof DERIVATION_KINDS[number]; setKind: (k: typeof DERIVATION_KINDS[number]) => void;
  column: string; setColumn: (s: string) => void;
  constant: string; setConstant: (s: string) => void;
  defaultValue: string; setDefaultValue: (s: string) => void;
  datasetColumns: string[];
}> = ({
  name, setName, length, setLength, kind, setKind,
  column, setColumn, constant, setConstant,
  defaultValue, setDefaultValue, datasetColumns,
}) => (
  <div className="space-y-3">
    <div className="grid grid-cols-3 gap-3">
      <div className="col-span-2">
        <label className="label">Segment name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Company / CostCenter / NaturalAccount …" />
      </div>
      <div>
        <label className="label">Length</label>
        <input type="number" min={1} max={20} className="input"
          value={length} onChange={(e) => setLength(Number(e.target.value))} />
      </div>
    </div>
    <div>
      <label className="label">Derivation</label>
      <select className="input" value={kind} onChange={(e) => setKind(e.target.value as any)}>
        {DERIVATION_KINDS.map((k) => <option key={k}>{k}</option>)}
      </select>
    </div>
    {kind === "constant" ? (
      <div>
        <label className="label">Constant value</label>
        <input className="input font-mono" value={constant}
          onChange={(e) => setConstant(e.target.value)} />
      </div>
    ) : (
      <div>
        <label className="label">Source column</label>
        <select className="input" value={column} onChange={(e) => setColumn(e.target.value)}>
          <option value="">— pick a column —</option>
          {datasetColumns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
    )}
    <div>
      <label className="label">Default (when blank/unmapped)</label>
      <input className="input font-mono" value={defaultValue}
        onChange={(e) => setDefaultValue(e.target.value)} placeholder="0000" />
    </div>
  </div>
);

const SegmentCrosswalkDrawer: React.FC<{
  segment: COASegment;
  onClose: () => void;
  onChanged: () => void;
}> = ({ segment, onClose, onChanged }) => {
  const [rows, setRows] = useState<COACrosswalk[] | null>(null);
  const [legacy, setLegacy] = useState("");
  const [fusion, setFusion] = useState("");
  const [busy, setBusy] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const reload = () => COAApi.crosswalks(segment.id).then(setRows);
  useEffect(() => { reload(); }, [segment.id]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`Crosswalk · ${segment.name}`}
      size="lg"
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}
    >
      <div className="space-y-3">
        <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
          <input className="input font-mono" placeholder="Legacy value"
            value={legacy} onChange={(e) => setLegacy(e.target.value)} />
          <input className="input font-mono" placeholder="Fusion value"
            value={fusion} onChange={(e) => setFusion(e.target.value)} />
          <Button
            loading={busy}
            disabled={!legacy.trim() || !fusion.trim()}
            onClick={async () => {
              setBusy(true);
              try {
                await COAApi.upsertCrosswalk(segment.id, {
                  legacy_value: legacy, fusion_value: fusion,
                });
                setLegacy(""); setFusion("");
                await reload();
              } finally { setBusy(false); }
            }}
            className="!h-9"
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
        </div>
        <button
          onClick={() => setBulkOpen((o) => !o)}
          className="inline-flex items-center gap-1 text-[11.5px] font-medium text-brand-dark hover:underline"
        >
          <Upload className="h-3 w-3" /> Bulk upload (paste CSV: legacy_value,fusion_value)
        </button>
        {bulkOpen && (
          <div className="rounded-md border border-line bg-canvas p-3">
            <textarea
              className="input min-h-[120px] font-mono text-[11.5px]"
              placeholder={"DEPT01,DEPT_NORTH\nDEPT02,DEPT_SOUTH\n…"}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
            />
            <div className="mt-2 flex justify-end">
              <Button
                onClick={async () => {
                  const parsed = bulkText
                    .split(/\r?\n/)
                    .map((line) => line.split(","))
                    .filter((p) => p.length >= 2 && p[0].trim())
                    .map((p) => ({
                      legacy_value: p[0].trim(),
                      fusion_value: p[1].trim(),
                    }));
                  if (parsed.length === 0) return;
                  await COAApi.bulkUpsertCrosswalk(segment.id, parsed);
                  setBulkText("");
                  setBulkOpen(false);
                  await reload();
                }}
              >
                <Upload className="h-3.5 w-3.5" /> Upsert
              </Button>
            </div>
          </div>
        )}
        {!rows ? (
          <Loader2 className="h-4 w-4 animate-spin text-ink-muted" />
        ) : rows.length === 0 ? (
          <div className="text-[12px] text-ink-muted">
            No rows yet — add one above or bulk-paste.
          </div>
        ) : (
          <table className="table-shell !text-[12px]">
            <thead>
              <tr>
                <th>Legacy</th>
                <th>Fusion</th>
                <th>Approved by</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="font-mono text-danger">{r.legacy_value}</td>
                  <td className="font-mono text-success">{r.fusion_value}</td>
                  <td className="text-[11px] text-ink-muted">{r.approved_by || "—"}</td>
                  <td className="text-right">
                    <button
                      onClick={async () => {
                        await COAApi.removeCrosswalk(r.id);
                        await reload();
                      }}
                      className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Modal>
  );
};

const CoveragePanel: React.FC<{ compose: COAComposeResult }> = ({ compose }) => {
  const tone = compose.coverage_pct >= 95 ? "text-success" : compose.coverage_pct >= 80 ? "text-warning" : "text-danger";
  return (
    <Card className="mt-4">
      <CardHeader
        title={<span className="inline-flex items-center gap-1.5"><Calculator className="h-4 w-4 text-brand" /> Composition coverage</span>}
        subtitle={`${compose.total_rows.toLocaleString()} source rows · sample below`}
        actions={
          <span className={cn("text-2xl font-bold tabular-nums", tone)}>
            {compose.coverage_pct.toFixed(1)}%
          </span>
        }
      />
      <CardBody>
        <div className="grid grid-cols-3 gap-2">
          <Tile label="Total rows" value={compose.total_rows.toLocaleString()} />
          <Tile label="Valid composed" value={compose.valid_rows.toLocaleString()} tone="text-success" />
          <Tile label="Invalid (gaps)" value={compose.invalid_rows.toLocaleString()} tone="text-danger" />
        </div>

        <div className="mt-3 rounded-md border border-line">
          <div className="border-b border-line bg-canvas px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Per-segment coverage
          </div>
          <table className="table-shell !text-[12px]">
            <thead>
              <tr><th>Segment</th><th>Coverage</th><th>Failed</th><th>Sample unmapped legacy values</th></tr>
            </thead>
            <tbody>
              {Object.entries(compose.per_segment_coverage).map(([name, c]) => (
                <tr key={name}>
                  <td className="font-medium text-ink">{name}</td>
                  <td><Pill tone={c.coverage_pct >= 95 ? "success" : c.coverage_pct >= 80 ? "warning" : "danger"} className="!text-[10px]">
                    {c.coverage_pct.toFixed(1)}%
                  </Pill></td>
                  <td className="font-mono text-[11px] text-ink-muted">{c.failed}</td>
                  <td className="font-mono text-[11px] text-ink-muted">
                    {(compose.per_segment_unmapped_values[name] || []).slice(0, 8).join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-3 rounded-md border border-line">
          <div className="border-b border-line bg-canvas px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Sample composed accounts
          </div>
          <table className="table-shell !text-[12px]">
            <thead>
              <tr>
                <th className="!w-12">#</th>
                <th>Composed account</th>
                <th>Valid?</th>
                <th>First failure</th>
              </tr>
            </thead>
            <tbody>
              {compose.sample_rows.map((r) => {
                const firstFail = r.emissions.find((e) => !e.valid);
                return (
                  <tr key={r.source_index}>
                    <td className="font-mono text-[11px] text-ink-muted">{r.source_index}</td>
                    <td className={cn("font-mono", r.valid ? "text-ink" : "text-danger")}>
                      {r.composed_account || "(empty)"}
                    </td>
                    <td>
                      {r.valid ? (
                        <Pill tone="success" className="!text-[10px]">valid</Pill>
                      ) : (
                        <Pill tone="danger" className="!text-[10px]">gap</Pill>
                      )}
                    </td>
                    <td className="text-[11px] text-ink-muted">
                      {firstFail ? `${firstFail.segment}: ${firstFail.reason}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
};

const Tile: React.FC<{ label: string; value: string; tone?: string }> = ({ label, value, tone }) => (
  <div className="rounded-md border border-line bg-white px-3 py-2">
    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">{label}</div>
    <div className={cn("mt-1 font-mono text-xl font-semibold tabular-nums", tone || "text-ink")}>{value}</div>
  </div>
);
