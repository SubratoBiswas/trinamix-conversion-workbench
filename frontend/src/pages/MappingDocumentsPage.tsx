import React, { useEffect, useMemo, useState } from "react";
import { UploadCloud, AlertTriangle, Check, X, Trash2, RefreshCw, FileSpreadsheet, Sparkles, Download } from "lucide-react";
import { LearningApi, ClientsApi } from "@/api";
import { Button } from "@/components/ui/Primitives";

type Row = {
  row_no: number; target_object: string; target_field: string; source_field: string;
  source_alternatives?: string[]; fbdi_sheet?: string | null; source_system?: string | null;
  rule_type?: string | null;
  status: "new" | "unchanged" | "conflict"; decision: "pending" | "approved" | "rejected";
  current_source_field?: string | null; current_rule_type?: string | null;
  current_captured_from?: string | null; conflict_reason?: string | null;
  is_learnt?: boolean; learnt_from?: string | null;
  gold_source?: string | null; gold_note?: string | null;
  ai_verdict?: string | null; ai_recommends?: string | null; ai_reason?: string | null;
  override_source?: string | null; override_reason?: string | null;
  notes?: string | null;
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

  const [vetting, setVetting] = useState(false);
  const vetWithAi = async () => {
    if (!open) return;
    setVetting(true); setMsg(null);
    try {
      const r = await LearningApi.vetProposal(open.id);
      setOpen(await LearningApi.getProposal(open.id));
      setMsg(r.vetted > 0 ? `AI reviewed ${r.vetted} row(s). Recommendations shown in the table.`
        : "AI review returned nothing (already reviewed, or AI unavailable).");
    } catch {
      setMsg("AI review is unavailable right now.");
    } finally { setVetting(false); }
  };

  const override = async (rowNo: number, currentSource: string) => {
    if (!open) return;
    const source = window.prompt("Override the source column for this field:", currentSource || "");
    if (source === null || !source.trim()) return;
    const reason = window.prompt("Reason for the override (required):", "");
    if (reason === null || !reason.trim()) {
      setMsg("Override cancelled — a reason is required.");
      return;
    }
    setBusy(true);
    try {
      await LearningApi.overrideProposalRow(open.id, rowNo, source.trim(), reason.trim());
      setOpen(await LearningApi.getProposal(open.id));
      setMsg("Override recorded — it will apply with your reason in the audit trail.");
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not override.");
    } finally { setBusy(false); }
  };

  const exportCsv = () => {
    if (!open) return;
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const header = [
      "Object", "Oracle field (destination)", "Proposed source", "Alternatives",
      "Status", "Already learnt", "Learnt from", "Currently mapped from",
      "Previous gold", "AI verdict", "AI recommends", "AI reason", "Why it conflicts",
      "Override source", "Override reason", "Decision", "Rule", "Source system", "Notes",
    ];
    const lines = (open.rows || []).map((r) => [
      r.target_object, r.target_field, r.source_field, (r.source_alternatives || []).join(" | "),
      r.status, r.is_learnt ? "YES" : "", r.learnt_from || "", r.current_source_field || "",
      r.gold_source || "", r.ai_verdict || "", r.ai_recommends || "", r.ai_reason || "",
      r.conflict_reason || "", r.override_source || "", r.override_reason || "",
      r.decision, r.rule_type || "", r.source_system || "", r.notes || "",
    ].map(esc).join(","));
    const csv = [header.map(esc).join(","), ...lines].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${open.file_name.replace(/\.[^.]+$/, "")}_mapping_review.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
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
            <div className="ml-auto flex flex-wrap gap-2">
              <Button variant="secondary" disabled={vetting || busy} onClick={vetWithAi}>
                <Sparkles size={14} /> {vetting ? "Reviewing…" : "Vet with AI"}
              </Button>
              <Button variant="secondary" disabled={!open.rows?.length} onClick={exportCsv}>
                <Download size={14} /> Export CSV
              </Button>
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

          <div className="max-h-[30rem] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-4 py-2">Object</th>
                  <th className="px-3 py-2">Source → Destination</th>
                  <th className="px-3 py-2">Learning</th>
                  <th className="px-3 py-2">Prev. gold</th>
                  <th className="px-3 py-2">AI review</th>
                  <th className="px-3 py-2">Why / conflict</th>
                  <th className="px-3 py-2">Decision</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const effectiveSource = r.override_source || r.source_field;
                  const verdictTone = r.ai_verdict === "wrong" ? "bg-rose-100 text-rose-700"
                    : r.ai_verdict === "unlikely" ? "bg-amber-100 text-amber-800"
                    : r.ai_verdict === "plausible" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500";
                  return (
                  <tr key={r.row_no} className="border-t align-top">
                    <td className="px-4 py-2 text-xs">
                      {r.target_object}
                      {r.fbdi_sheet && <div className="text-[10px] text-slate-400">{r.fbdi_sheet}</div>}
                    </td>
                    {/* source → destination */}
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <code className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-800">{effectiveSource}</code>
                        <span className="text-slate-400">→</span>
                        <span className="font-medium">{r.target_field}</span>
                        {r.rule_type && <Pill tone="bg-violet-50 text-violet-700">{r.rule_type}</Pill>}
                      </div>
                      {r.override_source && (
                        <div className="mt-0.5 text-[10px] text-violet-700">overridden — {r.override_reason}</div>
                      )}
                      {!!(r.source_alternatives?.length) && (
                        <div className="mt-0.5 text-[10px] text-slate-400">alt: {r.source_alternatives!.join(", ")}</div>
                      )}
                    </td>
                    {/* learning state */}
                    <td className="px-3 py-2 text-xs">
                      {r.is_learnt
                        ? <div>
                            <Pill tone="bg-indigo-50 text-indigo-700">already learnt</Pill>
                            {r.learnt_from && <div className="mt-0.5 text-[10px] text-slate-400">{r.learnt_from}</div>}
                          </div>
                        : <span className="text-slate-400">new</span>}
                    </td>
                    {/* previous gold */}
                    <td className="px-3 py-2 text-xs">
                      {r.gold_source
                        ? <span className="text-teal-700" title={r.gold_note || ""}>{r.gold_source}</span>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    {/* AI review */}
                    <td className="px-3 py-2 text-xs">
                      {r.ai_verdict
                        ? <div>
                            <Pill tone={verdictTone}>{r.ai_verdict}{r.ai_recommends ? ` · ${r.ai_recommends}` : ""}</Pill>
                            {r.ai_reason && <div className="mt-0.5 text-[10px] text-slate-500">{r.ai_reason}</div>}
                          </div>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    {/* why / conflict */}
                    <td className="px-3 py-2 text-[11px] text-muted">
                      {r.status === "conflict"
                        ? <span><span className="text-rose-700">now: {r.current_source_field}</span> — {r.conflict_reason}</span>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    {/* decision + override */}
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-1">
                        {r.status !== "conflict"
                          ? <Pill tone="bg-slate-100 text-slate-500">{r.decision !== "pending" ? r.decision : r.status}</Pill>
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
                        {open.status !== "applied" && (
                          <button onClick={() => override(r.row_no, effectiveSource)}
                                  className="text-[10px] text-violet-700 hover:underline">
                            Override…
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  );
                })}
                {!rows.length && (
                  <tr><td colSpan={7} className="px-4 py-6 text-center text-sm text-muted">
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
