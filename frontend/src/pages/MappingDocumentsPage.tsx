import React, { useEffect, useMemo, useState } from "react";
import { UploadCloud, AlertTriangle, Check, X, Trash2, RefreshCw, FileSpreadsheet, Sparkles, Download } from "lucide-react";
import { LearningApi, ClientsApi, FbdiApi } from "@/api";
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
  const [mode, setMode] = useState<"upload" | "manual">("upload");

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
          Upload an analyst mapping workbook, or map a template by hand. Either way the result is
          compared against what the tool already knows, and nothing reaches the library until you apply it.
        </p>
      </div>

      {/* mode toggle */}
      <div className="inline-flex rounded-lg border bg-white p-0.5 text-sm">
        <button onClick={() => setMode("upload")}
                className={`rounded-md px-3 py-1.5 ${mode === "upload" ? "bg-ink text-white" : "text-muted"}`}>
          Upload document
        </button>
        <button onClick={() => setMode("manual")}
                className={`rounded-md px-3 py-1.5 ${mode === "manual" ? "bg-ink text-white" : "text-muted"}`}>
          Map manually
        </button>
      </div>

      {mode === "manual" && (
        <ManualMapper clients={clients} />
      )}

      {mode === "upload" && (<>
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
      </>)}
    </div>
  );
};

// ───────────────────── Manual template mapper ─────────────────────
const ManualMapper: React.FC<{ clients: any[] }> = ({ clients }) => {
  const [templates, setTemplates] = useState<any[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [clientId, setClientId] = useState("");
  const [sys, setSys] = useState("");
  const [fields, setFields] = useState<any[]>([]);
  const [edits, setEdits] = useState<Record<string, { source: string; rule: string; reason?: string; ai?: boolean }>>({});
  const [verdicts, setVerdicts] = useState<Record<string, { plausible: boolean; reason: string; ai_verdict?: string; ai_reason?: string }>>({});
  const [reason, setReason] = useState("");
  const [useAi, setUseAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [proposals, setProposals] = useState<any[]>([]);
  const [proposalId, setProposalId] = useState("");
  const [joinSep, setJoinSep] = useState(" ");
  const [sourceList, setSourceList] = useState<string[]>([]);
  // filterable pick-list modal (source columns / destination fields)
  const [picker, setPicker] = useState<{ open: boolean; title: string; items: string[]; multi: boolean; sel: string[]; filter: string; resolve?: (v: string[] | null) => void }>(
    { open: false, title: "", items: [], multi: false, sel: [], filter: "" });
  const openPicker = (title: string, items: string[], multi: boolean, initial: string[] = []) =>
    new Promise<string[] | null>((resolve) => setPicker({ open: true, title, items, multi, sel: initial, filter: "", resolve }));
  const closePicker = (v: string[] | null) => { picker.resolve?.(v); setPicker((p) => ({ ...p, open: false, resolve: undefined })); };

  useEffect(() => { FbdiApi.list().then(setTemplates).catch(() => {}); }, []);

  const chosen = templates.find((t) => t.id === templateId);
  const targetObject = chosen?.business_object || chosen?.name || "";

  // offer uploaded documents that cover this object, so their proposal can prefill the grid
  useEffect(() => {
    if (!targetObject) { setProposals([]); return; }
    LearningApi.listProposals().then((ps) =>
      setProposals(ps.filter((p: any) => !p.target_object || String(p.target_object).toLowerCase() === targetObject.toLowerCase()))
    ).catch(() => setProposals([]));
  }, [targetObject]);

  const load = async () => {
    if (!templateId || !targetObject) return;
    setBusy(true); setMsg(null); setVerdicts({});
    try {
      const ctx = await LearningApi.manualContext(targetObject, templateId, clientId || undefined, proposalId || undefined);
      setFields(ctx.fields);
      // known source columns for this client → the filterable pick-list
      LearningApi.manualSources(clientId || undefined, targetObject).then((s) => setSourceList(s.sources || [])).catch(() => {});
      // prefill: document's proposal wins (if chosen), else the learnt source
      const e: Record<string, { source: string; rule: string; reason?: string }> = {};
      for (const f of ctx.fields) {
        e[f.target_field] = { source: f.doc_source || f.learnt_source || "", rule: f.learnt_rule || "" };
      }
      setEdits(e);
      setMsg(`${ctx.fields.length} fields · ${ctx.learnt_count} already learnt · ${ctx.gold_count} in a previous gold output`
        + (ctx.doc_count ? ` · ${ctx.doc_count} pre-filled from the document` : "") + ".");
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not load the template fields.");
    } finally { setBusy(false); }
  };

  const vet = async () => {
    const pairs = fields
      .map((f) => ({ target_field: f.target_field, source_field: (edits[f.target_field]?.source || "").trim() }))
      .filter((p) => p.source_field);
    if (!pairs.length) return;
    setBusy(true);
    try {
      const r = await LearningApi.manualVet(pairs, useAi);
      const v: typeof verdicts = {};
      for (const x of r.results) v[x.target_field] = x;
      setVerdicts(v);
      setMsg(`Checked ${r.results.length} pair(s)${useAi ? " with AI" : ""}.`);
    } catch { setMsg("Vetting is unavailable right now."); }
    finally { setBusy(false); }
  };

  // Overriding a field that is ALREADY LEARNT requires a reason. This flags such a
  // change (learnt, and the source now differs from what was learnt).
  const changedLearnt = (f: any) => {
    const cur = (edits[f.target_field]?.source || "").trim();
    return f.learnt_source && cur && cur.toLowerCase() !== String(f.learnt_source).toLowerCase();
  };

  // The source is chosen from the filterable list in the row's input; Override only
  // captures the mandatory reason for changing an already-learnt mapping.
  const overrideField = (f: any) => {
    const ed = edits[f.target_field] || { source: "", rule: "" };
    const why = window.prompt(`Reason for overriding "${f.target_field}" (required). Pick the source column from the list in the row first.`, ed.reason || "");
    if (why === null || !why.trim()) { setMsg("Override cancelled — a reason is required."); return; }
    setEdits((p) => ({ ...p, [f.target_field]: { ...ed, reason: why.trim() } }));
  };

  // Opt-in: fill ONLY the fields that have no source yet (no learning, gold, doc or
  // typed value). Never overwrites an existing mapping.
  const aiFillBlanks = async () => {
    const blanks = fields.filter((f) => !(edits[f.target_field]?.source || "").trim());
    if (!blanks.length) { setMsg("No blank fields to fill — everything already has a source."); return; }
    if (!sourceList.length) { setMsg("No known source columns for this client yet — upload a dataset or a document first."); return; }
    if (!window.confirm(`Ask AI to suggest a source for ${blanks.length} unmapped field(s)? It will not touch fields that already have a mapping.`)) return;
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.manualSuggestFill(blanks.map((f) => f.target_field), sourceList);
      if (!r.filled.length) { setMsg("AI did not find confident matches for the blank fields."); return; }
      setEdits((p) => {
        const next = { ...p };
        for (const m of r.filled) {
          // guard: only fill if still blank (don't clobber a value typed meanwhile)
          if (!(next[m.target_field]?.source || "").trim()) {
            const ed = next[m.target_field] || { source: "", rule: "" };
            next[m.target_field] = { ...ed, source: m.source, ai: true };
          }
        }
        return next;
      });
      setMsg(`AI suggested sources for ${r.filled.length} of ${blanks.length} blank field(s). Review the highlighted rows and Save.`);
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "AI fill unavailable.");
    } finally { setBusy(false); }
  };

  // Many sources → one field, launched from the toolbar: pick the destination field
  // from the list first, then the source picker opens.
  const aiManyToOneToolbar = async () => {
    const pick = await openPicker("Pick the destination field to build from several sources", fields.map((f) => f.target_field), false);
    if (!pick || !pick.length) return;
    const f = fields.find((x) => x.target_field === pick[0]);
    if (f) await aiManyToOne(f);
  };

  // AI: one source → many destination fields. Ask which target fields the source
  // fits, then fill those rows.
  const aiOneToMany = async () => {
    const pick = await openPicker("Pick a source column to spread across fields", sourceList, false);
    if (!pick || !pick.length) return;
    const source = pick[0];
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.manualSuggestOneToMany(source, fields.map((f) => f.target_field));
      if (!r.matches.length) { setMsg(`AI found no fields that "${source}" fits.`); return; }
      setEdits((p) => {
        const next = { ...p };
        for (const m of r.matches) {
          const ed = next[m.target_field] || { source: "", rule: "" };
          next[m.target_field] = { ...ed, source };
        }
        return next;
      });
      setMsg(`AI mapped "${source}" into ${r.matches.length} field(s): ${r.matches.map((m) => m.target_field).join(", ")}. Review and Save.`);
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "AI suggestion unavailable.");
    } finally { setBusy(false); }
  };

  // AI: many source columns → one destination field. Pick candidates from the list,
  // then AI decides which combine and how; set that field's sources + rule.
  const aiManyToOne = async (f: any) => {
    const preset = (edits[f.target_field]?.source || "").split(",").map((s) => s.trim()).filter(Boolean);
    const pick = await openPicker(`Pick source columns that could fill "${f.target_field}"`, sourceList, true, preset);
    if (!pick || !pick.length) return;
    const sources = pick;
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.manualSuggestManyToOne(f.target_field, sources);
      if (!r.use.length) { setMsg("AI did not find a combination for this field."); return; }
      const ed = edits[f.target_field] || { source: "", rule: "" };
      const rule = r.rule_type && r.rule_type !== "DIRECT" ? r.rule_type : "";
      setEdits((p) => ({ ...p, [f.target_field]: { ...ed, source: r.use.join(", "), rule } }));
      if (r.separator) setJoinSep(r.separator);
      setMsg(`AI: ${f.target_field} ← ${r.use.join(" + ")}${rule ? ` [${rule}]` : ""}. ${r.reason || ""} Review and Save.`);
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "AI suggestion unavailable.");
    } finally { setBusy(false); }
  };

  const save = async () => {
    // any learnt field whose source changed but has no reason blocks the save
    const needReason = fields.filter((f) => changedLearnt(f) && !(edits[f.target_field]?.reason || reason).trim());
    if (needReason.length) {
      setMsg(`A reason is required to override ${needReason.length} already-learnt field(s): ${needReason.slice(0, 4).map((f) => f.target_field).join(", ")}${needReason.length > 4 ? "…" : ""}. Use Override… on the row, or fill the Reason box.`);
      return;
    }
    const rows = fields
      .map((f) => {
        const source = (edits[f.target_field]?.source || "").trim();
        const multi = source.includes(",");
        return {
          target_field: f.target_field, source_field: source,
          rule_type: (edits[f.target_field]?.rule || "").trim() || undefined,
          separator: multi ? joinSep : undefined,
          // per-row override reason wins, else the global reason box
          reason: (edits[f.target_field]?.reason || reason).trim() || undefined,
        };
      })
      .filter((r) => r.source_field);
    if (!rows.length) { setMsg("Nothing to save — fill at least one source column."); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await LearningApi.manualSave({ client_id: clientId || undefined, target_object: targetObject, source_system: sys || undefined, rows });
      setMsg(`Saved ${r.saved} new · updated ${r.updated} · ${r.conversions_touched} existing conversion(s) refreshed.`);
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not save.");
    } finally { setBusy(false); }
  };

  const shown = fields.filter((f) => !q || f.target_field.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="rounded-xl border bg-white p-4 space-y-3">
      <div className="grid gap-3 md:grid-cols-4">
        <select className="rounded-lg border px-3 py-2 text-sm" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">Client — all (global)</option>
          {clients.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select className="rounded-lg border px-3 py-2 text-sm" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
          <option value="">Choose FBDI template…</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.business_object ? ` · ${t.business_object}` : ""}</option>)}
        </select>
        <input className="rounded-lg border px-3 py-2 text-sm" placeholder="Source system (optional)" value={sys} onChange={(e) => setSys(e.target.value)} />
        <Button onClick={load} disabled={busy || !templateId}>{busy ? "Loading…" : "Load fields"}</Button>
      </div>
      {proposals.length > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted">Prefill from an uploaded document:</span>
          <select className="rounded-lg border px-3 py-1.5 text-sm" value={proposalId} onChange={(e) => setProposalId(e.target.value)}>
            <option value="">— none (use learnt sources) —</option>
            {proposals.map((p) => <option key={p.id} value={p.id}>{p.file_name}</option>)}
          </select>
          <span className="text-[11px] text-muted">then Load fields</span>
        </div>
      )}
      {msg && <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm">{msg}</div>}

      {fields.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <input className="rounded-lg border px-3 py-1.5 text-sm" placeholder="Filter fields…" value={q} onChange={(e) => setQ(e.target.value)} />
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> include AI in check
            </label>
            <Button variant="secondary" disabled={busy} onClick={vet}><Sparkles size={14} /> Check mappings</Button>
            <Button variant="secondary" disabled={busy} onClick={aiOneToMany} title="One source column → the fields it fits (AI)">
              <Sparkles size={14} /> 1 source → many fields
            </Button>
            <Button variant="secondary" disabled={busy} onClick={aiManyToOneToolbar} title="Combine several source columns into one destination field (AI)">
              <Sparkles size={14} /> many sources → 1 field
            </Button>
            <Button variant="secondary" disabled={busy} onClick={aiFillBlanks} title="Ask AI to suggest a source only for fields that are still unmapped — never overwrites existing mappings">
              <Sparkles size={14} /> Fill blanks with AI
            </Button>
            <label className="flex items-center gap-1 text-xs text-muted">
              join with
              <input className="w-16 rounded border px-1.5 py-1 text-[11px]" value={joinSep} onChange={(e) => setJoinSep(e.target.value)} title="Separator used when a field has several source columns (CONCAT)" />
            </label>
            <input className="min-w-48 flex-1 rounded-lg border px-3 py-1.5 text-sm" placeholder="Reason (recorded on save, optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button disabled={busy} onClick={save}>Save mappings</Button>
          </div>
          <p className="text-[11px] text-muted">
            One source can feed several fields — just type it into each row. Several sources can feed one
            field — type them comma-separated (e.g. <code>First Name, Last Name</code>) and they save as a
            combined value using the separator above.
          </p>

          <datalist id="manual-source-columns">
            {sourceList.map((s) => <option key={s} value={s} />)}
          </datalist>
          {sourceList.length > 0 && (
            <p className="text-[11px] text-muted">{sourceList.length} known source column(s) — the source boxes filter as you type.</p>
          )}
          <div className="max-h-[30rem] overflow-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-3 py-2">Oracle field (destination)</th>
                  <th className="px-3 py-2">Source column</th>
                  <th className="px-3 py-2">Rule</th>
                  <th className="px-3 py-2">Document</th>
                  <th className="px-3 py-2">Learning</th>
                  <th className="px-3 py-2">Prev. gold</th>
                  <th className="px-3 py-2">Check</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((f) => {
                  const ed = edits[f.target_field] || { source: "", rule: "" };
                  const v = verdicts[f.target_field];
                  const bad = v && (v.plausible === false || v.ai_verdict === "wrong" || v.ai_verdict === "unlikely");
                  return (
                    <tr key={f.target_field} className="border-t align-top">
                      <td className="px-3 py-1.5">
                        <span className="font-medium">{f.target_field}</span>
                        {f.required && <Pill tone="bg-rose-50 text-rose-700">req</Pill>}
                        {f.sheet_name && <div className="text-[10px] text-slate-400">{f.sheet_name}</div>}
                      </td>
                      <td className="px-3 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <input
                            list="manual-source-columns"
                            className={`w-full rounded border px-2 py-1 text-[12px] ${bad ? "border-rose-300 bg-rose-50" : ed.ai ? "border-amber-300 bg-amber-50" : changedLearnt(f) ? "border-violet-300 bg-violet-50" : ""}`}
                            value={ed.source}
                            placeholder="pick or type a source column…"
                            onChange={(e) => setEdits((p) => ({ ...p, [f.target_field]: { ...ed, source: e.target.value, ai: false } }))}
                          />
                          {ed.ai && <span className="shrink-0 rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-800">AI</span>}
                          <button onClick={() => aiManyToOne(f)}
                                  title="Combine several source columns into this one field (AI)"
                                  className="shrink-0 text-[10px] font-medium text-brand hover:underline">
                            +AI
                          </button>
                          <button onClick={() => overrideField(f)}
                                  title={f.learnt_source ? "Override this learnt mapping (reason required)" : "Set with a reason"}
                                  className="shrink-0 text-[10px] font-medium text-violet-700 hover:underline">
                            Override…
                          </button>
                        </div>
                        {ed.source.includes(",") && (
                          <div className="mt-0.5 text-[10px] text-violet-700">combines {ed.source.split(",").filter((s) => s.trim()).length} columns (CONCAT)</div>
                        )}
                        {ed.reason && (
                          <div className="mt-0.5 text-[10px] text-violet-700">override — {ed.reason}</div>
                        )}
                        {changedLearnt(f) && !ed.reason && (
                          <div className="mt-0.5 text-[10px] text-rose-600">changed from "{f.learnt_source}" — reason required</div>
                        )}
                      </td>
                      <td className="px-3 py-1.5">
                        <input className="w-24 rounded border px-2 py-1 text-[11px]" value={ed.rule}
                               placeholder={ed.source.includes(",") ? "CONCAT" : "—"}
                               onChange={(e) => setEdits((p) => ({ ...p, [f.target_field]: { ...ed, rule: e.target.value } }))} />
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        {f.doc_source
                          ? <button className="text-emerald-700 hover:underline" title="Use the document's source"
                                    onClick={() => setEdits((p) => ({ ...p, [f.target_field]: { ...ed, source: f.doc_source } }))}>
                              {f.doc_source}
                            </button>
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        {f.learnt_source
                          ? <span className="text-indigo-700" title={f.learnt_from || ""}>{f.learnt_source}</span>
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        {f.gold_source ? <span className="text-teal-700">{f.gold_source}</span> : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-3 py-1.5 text-[11px]">
                        {v
                          ? <span className={bad ? "text-rose-700" : "text-emerald-700"}>
                              {v.ai_reason || v.reason || (v.plausible ? "looks fine" : "check this")}
                            </span>
                          : <span className="text-slate-300">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* filterable pick-list modal */}
      {picker.open && (() => {
        const filtered = picker.items.filter((it) => !picker.filter || it.toLowerCase().includes(picker.filter.toLowerCase()));
        const toggle = (it: string) => setPicker((p) => {
          if (!p.multi) return { ...p, sel: [it] };
          const has = p.sel.includes(it);
          return { ...p, sel: has ? p.sel.filter((x) => x !== it) : [...p.sel, it] };
        });
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => closePicker(null)}>
            <div className="w-full max-w-md rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
              <div className="border-b px-4 py-3 text-sm font-semibold">{picker.title}</div>
              <div className="p-3">
                <input autoFocus className="mb-2 w-full rounded-lg border px-3 py-2 text-sm" placeholder="Filter…"
                       value={picker.filter} onChange={(e) => setPicker((p) => ({ ...p, filter: e.target.value }))} />
                <div className="max-h-72 overflow-auto rounded-lg border">
                  {filtered.length === 0 && <div className="px-3 py-6 text-center text-sm text-muted">No matches.</div>}
                  {filtered.map((it) => {
                    const on = picker.sel.includes(it);
                    return (
                      <button key={it} onClick={() => { toggle(it); if (!picker.multi) closePicker([it]); }}
                              className={`flex w-full items-center gap-2 border-b px-3 py-1.5 text-left text-sm last:border-0 hover:bg-slate-50 ${on ? "bg-indigo-50" : ""}`}>
                        {picker.multi && <input type="checkbox" readOnly checked={on} />}
                        <span className="truncate">{it}</span>
                      </button>
                    );
                  })}
                </div>
                {picker.multi && (
                  <div className="mt-1 text-[11px] text-muted">{picker.sel.length} selected</div>
                )}
              </div>
              <div className="flex justify-end gap-2 border-t px-4 py-3">
                <Button variant="secondary" onClick={() => closePicker(null)}>Cancel</Button>
                {picker.multi && <Button disabled={!picker.sel.length} onClick={() => closePicker(picker.sel)}>Use {picker.sel.length || ""}</Button>}
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
};
