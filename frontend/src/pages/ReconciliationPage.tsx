import React, { useCallback, useEffect, useState } from "react";
import { Plus, CheckCircle, XCircle, Minus } from "lucide-react";
import { GovernanceApi } from "@/api";
import { Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill } from "@/components/ui/Primitives";

type ReconCheck = {
  id: string;
  check_name: string;
  check_type: string;
  source_value?: string;
  fusion_value?: string;
  tolerance: number;
  passed?: boolean;
  variance?: number;
  notes?: string;
  checked_at: string;
};

// Demo project — wire from URL params in production
const DEMO_PROJECT = "demo";

export const ReconciliationPage: React.FC = () => {
  const [checks, setChecks] = useState<ReconCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    check_name: "",
    check_type: "count",
    source_value: "",
    fusion_value: "",
    tolerance: "0",
    notes: "",
  });

  const load = useCallback(async () => {
    try {
      const data = await GovernanceApi.listRecon(DEMO_PROJECT);
      setChecks(data);
    } catch {
      setChecks([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = useCallback(async () => {
    const r = await GovernanceApi.createRecon({
      project_id: DEMO_PROJECT,
      check_name: form.check_name,
      check_type: form.check_type,
      source_value: form.source_value || undefined,
      fusion_value: form.fusion_value || undefined,
      tolerance: parseFloat(form.tolerance) || 0,
      notes: form.notes || undefined,
    });
    setChecks((cs) => [r, ...cs]);
    setShowForm(false);
    setForm({ check_name: "", check_type: "count", source_value: "", fusion_value: "", tolerance: "0", notes: "" });
  }, [form]);

  const passed = checks.filter((c) => c.passed === true).length;
  const failed = checks.filter((c) => c.passed === false).length;
  const pending = checks.filter((c) => c.passed == null).length;

  if (loading) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Reconciliation"
        subtitle="Verify source counts and totals match Oracle Fusion post-load"
        actions={
          <button
            onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
          >
            <Plus className="h-3.5 w-3.5" /> Add Check
          </button>
        }
      />

      {/* Summary chips */}
      <div className="mb-4 flex gap-3">
        {[
          { label: "Passed", value: passed, icon: <CheckCircle className="h-4 w-4 text-success" />, color: "text-success" },
          { label: "Failed", value: failed, icon: <XCircle className="h-4 w-4 text-danger" />, color: "text-danger" },
          { label: "Pending", value: pending, icon: <Minus className="h-4 w-4 text-ink-muted" />, color: "text-ink-muted" },
        ].map((m) => (
          <div key={m.label} className="flex items-center gap-2 rounded-lg border border-line bg-white px-4 py-2.5">
            {m.icon}
            <div>
              <div className={`text-lg font-bold ${m.color}`}>{m.value}</div>
              <div className="text-[11px] text-ink-muted">{m.label}</div>
            </div>
          </div>
        ))}
      </div>

      {checks.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon={<CheckCircle className="h-5 w-5" />}
              title="No reconciliation checks"
              description="Add checks comparing source counts/totals to Oracle Fusion after each load."
            />
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardHeader title="Reconciliation Checks" subtitle={`${checks.length} checks`} />
          <CardBody>
            <div className="space-y-2">
              <div className="grid grid-cols-[2fr_1fr_1fr_1fr_80px] gap-2 px-2 text-[10px] font-medium text-ink-muted uppercase tracking-wide">
                <span>Check</span><span>Source</span><span>Fusion</span><span>Variance</span><span>Result</span>
              </div>
              {checks.map((c) => (
                <div
                  key={c.id}
                  className="grid grid-cols-[2fr_1fr_1fr_1fr_80px] items-center gap-2 rounded-md border border-line bg-white px-3 py-2.5"
                >
                  <div>
                    <div className="text-xs font-semibold text-ink">{c.check_name}</div>
                    <div className="text-[11px] text-ink-muted">{c.check_type}</div>
                  </div>
                  <div className="text-xs font-mono text-ink">{c.source_value ?? "—"}</div>
                  <div className="text-xs font-mono text-ink">{c.fusion_value ?? "—"}</div>
                  <div className="text-xs font-mono">
                    {c.variance != null
                      ? <span className={c.passed === false ? "text-danger" : "text-ink"}>{c.variance.toLocaleString()}</span>
                      : <span className="text-ink-muted">—</span>}
                  </div>
                  <div className="flex items-center gap-1">
                    {c.passed === true && <><CheckCircle className="h-3.5 w-3.5 text-success" /><span className="text-[11px] text-success">Pass</span></>}
                    {c.passed === false && <><XCircle className="h-3.5 w-3.5 text-danger" /><span className="text-[11px] text-danger">Fail</span></>}
                    {c.passed == null && <><Minus className="h-3.5 w-3.5 text-ink-muted" /><span className="text-[11px] text-ink-muted">TBD</span></>}
                  </div>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-base font-semibold">Add Reconciliation Check</h2>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Check Name</label>
                <input
                  value={form.check_name}
                  onChange={(e) => setForm((f) => ({ ...f, check_name: e.target.value }))}
                  className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm"
                  placeholder="e.g. AP Invoice Count"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Type</label>
                <select
                  value={form.check_type}
                  onChange={(e) => setForm((f) => ({ ...f, check_type: e.target.value }))}
                  className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm"
                >
                  {["count","sum","hash","sample"].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-ink-muted">Source Value</label>
                  <input
                    value={form.source_value}
                    onChange={(e) => setForm((f) => ({ ...f, source_value: e.target.value }))}
                    className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm font-mono"
                    placeholder="42000"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-ink-muted">Fusion Value</label>
                  <input
                    value={form.fusion_value}
                    onChange={(e) => setForm((f) => ({ ...f, fusion_value: e.target.value }))}
                    className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm font-mono"
                    placeholder="41998"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Tolerance (max acceptable variance)</label>
                <input
                  type="number"
                  value={form.tolerance}
                  onChange={(e) => setForm((f) => ({ ...f, tolerance: e.target.value }))}
                  className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm"
                  min="0"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Notes</label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  rows={2}
                  className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm"
                />
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={!form.check_name}
                className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                Add Check
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
