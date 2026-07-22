import React, { useEffect, useMemo, useState } from "react";
import { UploadCloud, AlertTriangle, Check, X, Trash2, RefreshCw, FileSpreadsheet } from "lucide-react";
import { LearningApi, ClientsApi } from "@/api";
import { Button } from "@/components/ui/Primitives";

type Row = {
  row_no: number; target_object: string; target_field: string; source_field: string;
  fbdi_sheet?: string | null; source_system?: string | null; rule_type?: string | null;
  status: "new" | "unchanged" | "conflict"; decision: "pending" | "approved" | "rejected";
  current_source_field?: string | null; current_rule_type?: string | null;
  current_captured_from?: string | null; conflict_reason?: string | null;
};
type Proposal = {
  id: string; file_name: string; target_object?: string | null; source_system?: string | null;
  status: string; layout_method: string; layout_note?: string | null;
  count_new: number; count_unchanged: number; count_conflict: number; count_skipped: number;
  learnings_written?: number; conversions_touched?: number;
  uploaded_at?: string; applied_at?: string | null; rows?: Row[];
};

const Pill: React.FC<{ tone: string; children: React.ReactNode }> = ({ tone, children }) => (
  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>{children}</span>
);

export const MappingDocumentsPage: React.FC = () => {
  const [list, setList] = useState<Proposal[]>([]);
  const [open, setOpen] = useState<Proposal | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [obj, setObj] = useState("");
  const [sys, setSys] = useState("");
  const [clientId, setClientId] = useState("");
  const [clients, setClients] = useState<any[]>([]);
  const [objects, setObjects] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [filter, setFilter] = useState<"conflict" | "new" | "unchanged" | "all">("conflict");

  const refresh = async () => setList(await LearningApi.listProposals());

  useEffect(() => {
    refresh().catch(() => {});
    // ClientsApi.list returns { clients, global }, not a bare array.
    ClientsApi.list().then(r => setClients(r?.clients || [])).catch(() => {});
    LearningApi.knownObjects().then(r => setObjects(r.objects || [])).catch(() => {});
  }, []);

  const analyze = async () => {
    if (!files.length) return;
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.analyzeProposal(files, {
        clientId: clientId || undefined, targetObject: obj || undefined,
        sourceSystem: sys || undefined,
      });
      const bad = r.proposals.find((p: any) => p.error);
      if (bad) setMsg(`${bad.file_name}: ${bad.error}`);
      setFiles([]);
      await refresh();
      const first = r.proposals.find((p: any) => p.id);
      if (first) setOpen(await LearningApi.getProposal(first.id));
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not analyse that file.");
    } finally { setBusy(false); }
  };

  const decide = async (d: string, rowNos?: number[]) => {
    if (!open) return;
    setBusy(true);
    try {
      await LearningApi.decideProposal(open.id, d, rowNos);
      setOpen(await LearningApi.getProposal(open.id));
    } finally { setBusy(false); }
  };

  const applyNow = async () => {
    if (!open) return;
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.applyProposal(open.id);
      setMsg(`Applied. ${r.learnings_written} mapping(s) written · ${r.conversions_touched} existing conversion(s) updated.`);
      await refresh();
      setOpen(await LearningApi.getProposal(open.id));
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not apply.");
    } finally { setBusy(false); }
  };

  const rows = useMemo(() => {
    const rs = open?.rows || [];
    return filter === "all" ? rs : rs.filter(r => r.status === filter);
  }, [open, filter]);

  const pendingConflicts = (open?.rows || []).filter(
    r => r.status === "conflict" && r.decision === "pending").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Mapping Documents</h1>
        <p className="text-sm text-muted">
          Upload an analyst mapping workbook. It is analysed and compared against what the tool
          already knows — anything that contradicts an existing mapping is held back for your
          approval. Nothing reaches the library until you apply it.
        </p>
      </div>

      {/* upload */}
      <div className="rounded-xl border bg-white p-4 space-y-3">
        <div className="grid gap-3 md:grid-cols-4">
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm">
            <UploadCloud size={16} />
            {files.length ? `${files.length} file(s) selected` : "Choose mapping file(s)"}
            <input type="file" multiple hidden accept=".xlsx,.xlsm,.xls,.csv,.tsv"
                   onChange={e => setFiles(Array.from(e.target.files || []))} />
          </label>
          <select className="rounded-lg border px-3 py-2 text-sm" value={clientId}
                  onChange={e => setClientId(e.target.value)}>
            <option value="">Client — all (global)</option>
            {clients.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select className="rounded-lg border px-3 py-2 text-sm" value={obj}
                  onChange={e => setObj(e.target.value)}>
            <option value="">Module — from the file</option>
            {objects.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input className="rounded-lg border px-3 py-2 text-sm" placeholder="Source system (optional)"
                 value={sys} onChange={e => setSys(e.target.value)} />
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={analyze} disabled={busy || !files.length}>
            {busy ? "Analysing…" : "Analyse"}
          </Button>
          <span className="text-xs text-muted">
            Set Module when the file has no object column — the Item tabs, for example.
          </span>
        </div>
        {msg && <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm">{msg}</div>}
      </div>

      {/* list */}
      <div className="rounded-xl border bg-white">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <h2 className="text-sm font-semibold">Uploaded documents</h2>
          <button onClick={refresh} className="text-muted hover:text-ink"><RefreshCw size={14} /></button>
        </div>
        {!list.length && <p className="px-4 py-6 text-sm text-muted">Nothing uploaded yet.</p>}
        {list.map(p => (
          <div key={p.id} className="flex items-center gap-3 border-b px-4 py-3 text-sm last:border-0">
            <FileSpreadsheet size={16} className="text-muted" />
            <button className="flex-1 text-left font-medium hover:underline"
                    onClick={async () => setOpen(await LearningApi.getProposal(p.id))}>
              {p.file_name}
              <span className="ml-2 text-xs font-normal text-muted">
                {p.target_object || "module from file"} · layout: {p.layout_method}
              </span>
            </button>
            <Pill tone="bg-emerald-50 text-emerald-700">{p.count_new} new</Pill>
            <Pill tone="bg-slate-100 text-slate-600">{p.count_unchanged} same</Pill>
            {p.count_conflict > 0
              ? <Pill tone="bg-amber-100 text-amber-800">{p.count_conflict} conflicting</Pill>
              : <Pill tone="bg-slate-100 text-slate-500">no conflicts</Pill>}
            <Pill tone={p.status === "applied" ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-600"}>
              {p.status === "applied" ? "applied" : "awaiting review"}
            </Pill>
            <button className="text-muted hover:text-rose-600"
                    onClick={async () => { await LearningApi.discardProposal(p.id); if (open?.id === p.id) setOpen(null); refresh(); }}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>

      {/* detail */}
      {open && (
        <div className="rounded-xl border bg-white">
          <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">{open.file_name}</h2>
            <Pill tone="bg-slate-100 text-slate-600">layout: {open.layout_method}</Pill>
            {open.status !== "applied" && pendingConflicts > 0 && (
              <span className="flex items-center gap-1 text-xs text-amber-700">
                <AlertTriangle size={13} /> {pendingConflicts} contradiction(s) need a decision
              </span>
            )}
            <div className="ml-auto flex gap-2">
              {open.status !== "applied" && (
                <>
                  <Button variant="secondary" disabled={busy} onClick={() => decide("approved")}>
                    <Check size={14} /> Approve all conflicts
                  </Button>
                  <Button variant="secondary" disabled={busy} onClick={() => decide("rejected")}>
                    <X size={14} /> Reject all conflicts
                  </Button>
                  <Button disabled={busy || pendingConflicts > 0} onClick={applyNow}>Apply</Button>
                </>
              )}
            </div>
          </div>
          {open.layout_note && (
            <p className="border-b bg-slate-50 px-4 py-2 text-xs text-muted">{open.layout_note}</p>
          )}
          {open.status === "applied" && (
            <p className="border-b bg-indigo-50 px-4 py-2 text-xs text-indigo-800">
              Applied — {open.learnings_written} mapping(s) written, {open.conversions_touched} existing
              conversion(s) updated.
            </p>
          )}

          <div className="flex gap-2 border-b px-4 py-2 text-xs">
            {(["conflict", "new", "unchanged", "all"] as const).map(f => (
              <button key={f} onClick={() => setFilter(f)}
                      className={`rounded-full px-3 py-1 ${filter === f ? "bg-ink text-white" : "bg-slate-100"}`}>
                {f === "conflict" ? `Conflicts (${open.count_conflict})`
                  : f === "new" ? `New (${open.count_new})`
                  : f === "unchanged" ? `Unchanged (${open.count_unchanged})` : "All"}
              </button>
            ))}
            {open.count_skipped > 0 && (
              <span className="ml-auto self-center text-muted">{open.count_skipped} row(s) skipped — no source or target</span>
            )}
          </div>

          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2">Object</th>
                  <th className="px-3 py-2">Oracle field</th>
                  <th className="px-3 py-2">Proposed source</th>
                  <th className="px-3 py-2">Currently</th>
                  <th className="px-3 py-2">Why it conflicts</th>
                  <th className="px-3 py-2">Decision</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.row_no} className="border-t align-top">
                    <td className="px-4 py-2 text-xs">{r.target_object}</td>
                    <td className="px-3 py-2 font-medium">{r.target_field}</td>
                    <td className="px-3 py-2 text-emerald-700">
                      {r.source_field}{r.rule_type ? ` [${r.rule_type}]` : ""}
                    </td>
                    <td className="px-3 py-2 text-rose-700">{r.current_source_field || "—"}</td>
                    <td className="px-3 py-2 text-xs text-muted">{r.conflict_reason || "—"}</td>
                    <td className="px-3 py-2">
                      {r.status !== "conflict"
                        ? <Pill tone="bg-slate-100 text-slate-500">{r.status}</Pill>
                        : open.status === "applied"
                          ? <Pill tone="bg-slate-100 text-slate-600">{r.decision}</Pill>
                          : (
                            <div className="flex gap-1">
                              <button onClick={() => decide("approved", [r.row_no])}
                                      className={`rounded px-2 py-0.5 text-xs ${r.decision === "approved" ? "bg-emerald-600 text-white" : "bg-slate-100"}`}>
                                Approve
                              </button>
                              <button onClick={() => decide("rejected", [r.row_no])}
                                      className={`rounded px-2 py-0.5 text-xs ${r.decision === "rejected" ? "bg-rose-600 text-white" : "bg-slate-100"}`}>
                                Reject
                              </button>
                            </div>
                          )}
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr><td colSpan={6} className="px-4 py-6 text-center text-sm text-muted">
                    Nothing in this view.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
