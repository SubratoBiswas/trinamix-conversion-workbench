import React, { useCallback, useEffect, useState } from "react";
import { Plus, Table2, ChevronRight, CheckCircle, Clock } from "lucide-react";
import { CoaApi } from "@/api";
import { Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

type Structure = { id: string; project_id: string; name: string; description?: string; legacy_system?: string; segment_count: number; total_values: number; created_at: string; };
type Segment = { id: string; structure_id: string; segment_name: string; segment_label?: string; segment_order: number; value_set_name?: string; };
type Crosswalk = { id: string; segment_id: string; legacy_value: string; legacy_description?: string; fusion_value?: string; fusion_description?: string; status: string; mapped_by?: string; };

// Demo project_id — in production read from URL params or context
const DEMO_PROJECT_ID = "demo";

export const CoaPage: React.FC = () => {
  const [structures, setStructures] = useState<Structure[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedStructure, setSelectedStructure] = useState<Structure | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [selectedSegment, setSelectedSegment] = useState<Segment | null>(null);
  const [crosswalks, setCrosswalks] = useState<Crosswalk[]>([]);
  const [stats, setStats] = useState<{ total: number; mapped: number; pending: number } | null>(null);
  const [showStructureForm, setShowStructureForm] = useState(false);
  const [newStructureName, setNewStructureName] = useState("");
  const [editingCw, setEditingCw] = useState<Record<string, string>>({});

  const loadStructures = useCallback(async () => {
    try {
      // Try to get a real project id from the URL, fallback to listing all
      const data = await CoaApi.listStructures(DEMO_PROJECT_ID);
      setStructures(data);
    } catch {
      setStructures([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStructures(); }, [loadStructures]);

  const handleSelectStructure = useCallback(async (s: Structure) => {
    setSelectedStructure(s);
    setSelectedSegment(null);
    setCrosswalks([]);
    try {
      const [segs, st] = await Promise.all([
        CoaApi.listSegments(s.id),
        CoaApi.stats(s.id),
      ]);
      setSegments(segs);
      setStats(st);
    } catch {
      setSegments([]);
    }
  }, []);

  const handleSelectSegment = useCallback(async (seg: Segment) => {
    setSelectedSegment(seg);
    try {
      const cws = await CoaApi.listCrosswalks(seg.id);
      setCrosswalks(cws);
      // Pre-fill edit state
      const edits: Record<string, string> = {};
      cws.forEach((c: Crosswalk) => { edits[c.id] = c.fusion_value ?? ""; });
      setEditingCw(edits);
    } catch {
      setCrosswalks([]);
    }
  }, []);

  const handleSaveCrosswalk = useCallback(async (cw: Crosswalk) => {
    const fv = editingCw[cw.id] ?? "";
    const updated = await CoaApi.updateCrosswalk(cw.id, {
      fusion_value: fv,
      status: fv ? "mapped" : "pending",
      mapped_by: "admin",
    });
    setCrosswalks((cs) => cs.map((c) => (c.id === cw.id ? updated : c)));
  }, [editingCw]);

  const handleCreateStructure = useCallback(async () => {
    if (!newStructureName) return;
    const s = await CoaApi.createStructure({ project_id: DEMO_PROJECT_ID, name: newStructureName });
    setStructures((ss) => [s, ...ss]);
    setShowStructureForm(false);
    setNewStructureName("");
  }, [newStructureName]);

  if (loading) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Chart of Accounts"
        subtitle="Map legacy segment values to Oracle Fusion COA segments"
        actions={
          <button
            onClick={() => setShowStructureForm(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
          >
            <Plus className="h-3.5 w-3.5" /> New Structure
          </button>
        }
      />

      <div className="flex gap-4">
        {/* COA structure list */}
        <div className="w-56 shrink-0 space-y-2">
          {structures.length === 0 ? (
            <Card>
              <CardBody>
                <EmptyState icon={<Table2 className="h-5 w-5" />} title="No structures" description="Create a COA structure to start mapping." />
              </CardBody>
            </Card>
          ) : (
            structures.map((s) => (
              <button
                key={s.id}
                onClick={() => handleSelectStructure(s)}
                className={cn(
                  "w-full rounded-lg border px-3 py-2.5 text-left transition",
                  selectedStructure?.id === s.id ? "border-brand bg-brand-subtle" : "border-line bg-white hover:border-brand-subtle"
                )}
              >
                <div className="text-xs font-semibold text-ink">{s.name}</div>
                <div className="mt-0.5 text-[11px] text-ink-muted">{s.segment_count} segments</div>
              </button>
            ))
          )}
        </div>

        {/* Middle: segments */}
        <div className="w-52 shrink-0 space-y-2">
          {!selectedStructure ? (
            <div className="flex h-32 items-center justify-center rounded-lg border border-line bg-white text-xs text-ink-muted">
              Select a structure →
            </div>
          ) : (
            <>
              {stats && (
                <div className="flex gap-2 rounded-lg border border-line bg-white px-3 py-2">
                  <div className="text-center flex-1">
                    <div className="text-base font-bold text-success">{stats.mapped}</div>
                    <div className="text-[10px] text-ink-muted">Mapped</div>
                  </div>
                  <div className="text-center flex-1">
                    <div className="text-base font-bold text-warning">{stats.pending}</div>
                    <div className="text-[10px] text-ink-muted">Pending</div>
                  </div>
                  <div className="text-center flex-1">
                    <div className="text-base font-bold text-ink">{stats.total}</div>
                    <div className="text-[10px] text-ink-muted">Total</div>
                  </div>
                </div>
              )}
              {segments.length === 0 ? (
                <div className="rounded-lg border border-line bg-white px-3 py-4 text-center text-xs text-ink-muted">No segments yet.</div>
              ) : (
                segments.map((seg) => (
                  <button
                    key={seg.id}
                    onClick={() => handleSelectSegment(seg)}
                    className={cn(
                      "flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left transition",
                      selectedSegment?.id === seg.id ? "border-brand bg-brand-subtle" : "border-line bg-white hover:border-brand-subtle"
                    )}
                  >
                    <div>
                      <div className="text-xs font-semibold text-ink">{seg.segment_name}</div>
                      {seg.segment_label && <div className="text-[11px] text-ink-muted">{seg.segment_label}</div>}
                    </div>
                    <ChevronRight className="h-3.5 w-3.5 text-ink-muted" />
                  </button>
                ))
              )}
            </>
          )}
        </div>

        {/* Right: crosswalk editor */}
        <div className="flex-1">
          {!selectedSegment ? (
            <Card>
              <CardBody>
                <EmptyState icon={<Table2 className="h-5 w-5" />} title="Select a segment" description="Pick a COA segment to review and map its values." />
              </CardBody>
            </Card>
          ) : (
            <Card>
              <CardHeader
                title={`${selectedSegment.segment_name} — Value Crosswalk`}
                subtitle={`${crosswalks.length} values`}
              />
              <CardBody>
                {crosswalks.length === 0 ? (
                  <div className="py-4 text-center text-sm text-ink-muted">No crosswalk values for this segment.</div>
                ) : (
                  <div className="space-y-2">
                    <div className="grid grid-cols-[1fr_24px_1fr_80px] gap-2 px-2 text-[10px] font-medium text-ink-muted uppercase tracking-wide">
                      <span>Legacy Value</span><span></span><span>Fusion Value</span><span>Status</span>
                    </div>
                    {crosswalks.map((cw) => (
                      <div key={cw.id} className="grid grid-cols-[1fr_24px_1fr_80px] items-center gap-2 rounded-md border border-line bg-white px-2 py-2">
                        <div>
                          <div className="text-xs font-mono font-medium text-ink">{cw.legacy_value}</div>
                          {cw.legacy_description && <div className="text-[11px] text-ink-muted">{cw.legacy_description}</div>}
                        </div>
                        <span className="text-center text-ink-muted">→</span>
                        <input
                          type="text"
                          value={editingCw[cw.id] ?? ""}
                          onChange={(e) => setEditingCw((m) => ({ ...m, [cw.id]: e.target.value }))}
                          onBlur={() => handleSaveCrosswalk(cw)}
                          className="rounded border border-line px-2 py-1 text-xs font-mono"
                          placeholder="Fusion value"
                        />
                        <div className="flex items-center gap-1">
                          {cw.status === "mapped"
                            ? <><CheckCircle className="h-3.5 w-3.5 text-success" /><span className="text-[11px] text-success">Mapped</span></>
                            : <><Clock className="h-3.5 w-3.5 text-warning" /><span className="text-[11px] text-warning">Pending</span></>
                          }
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      {showStructureForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-base font-semibold text-ink">New COA Structure</h2>
            <input
              type="text"
              value={newStructureName}
              onChange={(e) => setNewStructureName(e.target.value)}
              placeholder="e.g. Legacy Oracle EBS COA"
              className="w-full rounded-md border border-line px-3 py-2 text-sm"
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setShowStructureForm(false)} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-canvas">Cancel</button>
              <button
                onClick={handleCreateStructure}
                disabled={!newStructureName}
                className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
