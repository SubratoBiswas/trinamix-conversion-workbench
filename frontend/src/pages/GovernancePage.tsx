import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ShieldCheck, CheckSquare, PlayCircle, Plus, BarChart2 } from "lucide-react";
import { GovernanceApi } from "@/api";
import { Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

type Issue = { id: string; project_id: string; title: string; description?: string; severity: string; status: string; owner?: string; due_date?: string; created_at: string; };
type Risk = { id: string; project_id: string; title: string; description?: string; likelihood: string; impact: string; status: string; mitigation_plan?: string; owner?: string; created_at: string; };
type SignOff = { id: string; checkpoint: string; status: string; signed_off_by?: string; signed_off_at?: string; notes?: string; };
type Rehearsal = { id: string; name: string; status: string; outcome?: string; scheduled_at?: string; records_processed: number; records_failed: number; issues_found: number; };

type Summary = { open_issues: number; high_priority_issues: number; open_risks: number; pending_sign_offs: number; dress_rehearsals: number; reconciliation_checks: number; reconciliation_passed: number; };

type Tab = "issues" | "risks" | "signoffs" | "rehearsals";

const SEVERITY_TONE: Record<string, "danger" | "warning" | "default"> = {
  critical: "danger", high: "danger", medium: "warning", low: "default",
};
const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "default"> = {
  open: "warning", in_progress: "warning", resolved: "success", closed: "success",
  identified: "warning", mitigating: "warning", accepted: "default",
  pending: "warning", signed: "success", rejected: "danger",
  planned: "default", running: "warning", completed: "success", failed: "danger",
};

// Demo project id — wire from router params in production
const DEMO_PROJECT = "demo";

export const GovernancePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>("issues");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [signOffs, setSignOffs] = useState<SignOff[]>([]);
  const [rehearsals, setRehearsals] = useState<Rehearsal[]>([]);
  const [loading, setLoading] = useState(true);

  // Form state
  const [showIssueForm, setShowIssueForm] = useState(false);
  const [showRiskForm, setShowRiskForm] = useState(false);
  const [newIssue, setNewIssue] = useState({ title: "", severity: "medium", description: "", owner: "" });
  const [newRisk, setNewRisk] = useState({ title: "", likelihood: "medium", impact: "medium", description: "", mitigation_plan: "" });

  const reload = useCallback(async () => {
    try {
      const [sum, iss, rsk, so, reh] = await Promise.all([
        GovernanceApi.summary(DEMO_PROJECT).catch(() => null),
        GovernanceApi.listIssues(DEMO_PROJECT),
        GovernanceApi.listRisks(DEMO_PROJECT),
        GovernanceApi.listSignOffs(DEMO_PROJECT),
        GovernanceApi.listRehearsals(DEMO_PROJECT),
      ]);
      setSummary(sum);
      setIssues(iss);
      setRisks(rsk);
      setSignOffs(so);
      setRehearsals(reh);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const resolveIssue = useCallback(async (id: string) => {
    await GovernanceApi.updateIssue(id, { status: "resolved" });
    setIssues((is) => is.map((i) => (i.id === id ? { ...i, status: "resolved" } : i)));
  }, []);

  const signOff = useCallback(async (id: string) => {
    await GovernanceApi.updateSignOff(id, { status: "signed", signed_off_by: "admin" });
    setSignOffs((ss) => ss.map((s) => (s.id === id ? { ...s, status: "signed", signed_off_by: "admin" } : s)));
  }, []);

  const createIssue = useCallback(async () => {
    const i = await GovernanceApi.createIssue({ project_id: DEMO_PROJECT, ...newIssue });
    setIssues((is) => [i, ...is]);
    setShowIssueForm(false);
    setNewIssue({ title: "", severity: "medium", description: "", owner: "" });
  }, [newIssue]);

  const createRisk = useCallback(async () => {
    const r = await GovernanceApi.createRisk({ project_id: DEMO_PROJECT, ...newRisk });
    setRisks((rs) => [r, ...rs]);
    setShowRiskForm(false);
    setNewRisk({ title: "", likelihood: "medium", impact: "medium", description: "", mitigation_plan: "" });
  }, [newRisk]);

  const TABS: { key: Tab; label: string; icon: React.ReactNode; count?: number }[] = [
    { key: "issues", label: "Issues", icon: <AlertTriangle className="h-3.5 w-3.5" />, count: summary?.open_issues },
    { key: "risks", label: "Risks", icon: <ShieldCheck className="h-3.5 w-3.5" />, count: summary?.open_risks },
    { key: "signoffs", label: "Sign-offs", icon: <CheckSquare className="h-3.5 w-3.5" />, count: summary?.pending_sign_offs },
    { key: "rehearsals", label: "Dress Rehearsals", icon: <PlayCircle className="h-3.5 w-3.5" /> },
  ];

  if (loading) return <PageLoader />;

  return (
    <>
      <PageTitle title="Governance" subtitle="Issues, risks, sign-offs, and dress rehearsals" />

      {/* Summary row */}
      {summary && (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "Open Issues", value: summary.open_issues, tone: summary.open_issues > 0 ? "warning" : "success" },
            { label: "High Priority", value: summary.high_priority_issues, tone: summary.high_priority_issues > 0 ? "danger" : "success" },
            { label: "Open Risks", value: summary.open_risks, tone: summary.open_risks > 0 ? "warning" : "success" },
            { label: "Pending Sign-offs", value: summary.pending_sign_offs, tone: summary.pending_sign_offs > 0 ? "warning" : "success" },
          ].map((m) => (
            <div key={m.label} className="rounded-lg border border-line bg-white px-4 py-3">
              <div className={cn("text-2xl font-bold", m.tone === "danger" ? "text-danger" : m.tone === "warning" ? "text-warning" : "text-success")}>
                {m.value}
              </div>
              <div className="text-xs text-ink-muted">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="mb-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition",
              tab === t.key ? "border-brand text-brand" : "border-transparent text-ink-muted hover:text-ink"
            )}
          >
            {t.icon} {t.label}
            {t.count != null && t.count > 0 && (
              <span className="ml-0.5 rounded-full bg-warning-subtle px-1.5 py-0.5 text-[10px] font-bold text-warning">{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Issues */}
      {tab === "issues" && (
        <div className="space-y-3">
          <div className="flex justify-end">
            <button
              onClick={() => setShowIssueForm(true)}
              className="inline-flex items-center gap-1 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
            >
              <Plus className="h-3.5 w-3.5" /> Log Issue
            </button>
          </div>
          {issues.length === 0 ? (
            <Card><CardBody><EmptyState icon={<AlertTriangle className="h-5 w-5" />} title="No issues" description="The project has no logged issues." /></CardBody></Card>
          ) : (
            issues.map((i) => (
              <div key={i.id} className="flex items-start gap-3 rounded-lg border border-line bg-white px-4 py-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{i.title}</span>
                    <Pill tone={SEVERITY_TONE[i.severity] ?? "default"}>{i.severity}</Pill>
                    <Pill tone={STATUS_TONE[i.status] ?? "default"}>{i.status}</Pill>
                  </div>
                  {i.description && <div className="mt-1 text-xs text-ink-muted">{i.description}</div>}
                  {i.owner && <div className="mt-1 text-[11px] text-ink-muted">Owner: {i.owner}</div>}
                </div>
                {i.status === "open" && (
                  <button
                    onClick={() => resolveIssue(i.id)}
                    className="shrink-0 rounded-md border border-line px-2.5 py-1 text-[11px] font-medium text-ink hover:bg-success-subtle hover:text-success"
                  >
                    Resolve
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Risks */}
      {tab === "risks" && (
        <div className="space-y-3">
          <div className="flex justify-end">
            <button
              onClick={() => setShowRiskForm(true)}
              className="inline-flex items-center gap-1 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
            >
              <Plus className="h-3.5 w-3.5" /> Log Risk
            </button>
          </div>
          {risks.length === 0 ? (
            <Card><CardBody><EmptyState icon={<ShieldCheck className="h-5 w-5" />} title="No risks" description="No risks have been identified for this project." /></CardBody></Card>
          ) : (
            risks.map((r) => (
              <div key={r.id} className="rounded-lg border border-line bg-white px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-ink">{r.title}</span>
                  <Pill tone={SEVERITY_TONE[r.likelihood] ?? "default"}>L: {r.likelihood}</Pill>
                  <Pill tone={SEVERITY_TONE[r.impact] ?? "default"}>I: {r.impact}</Pill>
                  <Pill tone={STATUS_TONE[r.status] ?? "default"}>{r.status}</Pill>
                </div>
                {r.description && <div className="mt-1 text-xs text-ink-muted">{r.description}</div>}
                {r.mitigation_plan && (
                  <div className="mt-2 rounded-md bg-canvas px-2.5 py-1.5 text-[11px] text-ink-muted">
                    <span className="font-medium text-ink">Mitigation:</span> {r.mitigation_plan}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Sign-offs */}
      {tab === "signoffs" && (
        <div className="space-y-2">
          {signOffs.length === 0 ? (
            <Card><CardBody><EmptyState icon={<CheckSquare className="h-5 w-5" />} title="No sign-offs required" description="No governance checkpoints configured for this project." /></CardBody></Card>
          ) : (
            signOffs.map((s) => (
              <div key={s.id} className="flex items-center gap-3 rounded-lg border border-line bg-white px-4 py-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{s.checkpoint.replace(/_/g, " ")}</span>
                    <Pill tone={STATUS_TONE[s.status] ?? "default"}>{s.status}</Pill>
                  </div>
                  {s.signed_off_by && <div className="mt-0.5 text-[11px] text-ink-muted">Signed by {s.signed_off_by}</div>}
                </div>
                {s.status === "pending" && (
                  <button
                    onClick={() => signOff(s.id)}
                    className="shrink-0 rounded-md bg-success px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                  >
                    Sign Off
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Dress Rehearsals */}
      {tab === "rehearsals" && (
        <div className="space-y-3">
          {rehearsals.length === 0 ? (
            <Card><CardBody><EmptyState icon={<PlayCircle className="h-5 w-5" />} title="No dress rehearsals" description="Schedule a dress rehearsal to simulate the full migration pipeline." /></CardBody></Card>
          ) : (
            rehearsals.map((r) => (
              <div key={r.id} className="rounded-lg border border-line bg-white px-4 py-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{r.name}</span>
                    <Pill tone={STATUS_TONE[r.status] ?? "default"}>{r.status}</Pill>
                    {r.outcome && <Pill tone={r.outcome === "pass" ? "success" : "danger"}>{r.outcome}</Pill>}
                  </div>
                  {r.scheduled_at && (
                    <span className="text-[11px] text-ink-muted">
                      Scheduled: {new Date(r.scheduled_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <div className="mt-2 flex gap-4 text-xs text-ink-muted">
                  <span>{r.records_processed.toLocaleString()} processed</span>
                  <span className={r.records_failed > 0 ? "text-danger" : ""}>{r.records_failed} failed</span>
                  <span className={r.issues_found > 0 ? "text-warning" : ""}>{r.issues_found} issues</span>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Issue form modal */}
      {showIssueForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-base font-semibold">Log Issue</h2>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Title</label>
                <input value={newIssue.title} onChange={(e) => setNewIssue((f) => ({ ...f, title: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm" placeholder="Describe the issue" />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Severity</label>
                <select value={newIssue.severity} onChange={(e) => setNewIssue((f) => ({ ...f, severity: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm">
                  {["low","medium","high","critical"].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Owner</label>
                <input value={newIssue.owner} onChange={(e) => setNewIssue((f) => ({ ...f, owner: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm" placeholder="Email or name" />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Description</label>
                <textarea value={newIssue.description} onChange={(e) => setNewIssue((f) => ({ ...f, description: e.target.value }))} rows={3} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm" />
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setShowIssueForm(false)} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium">Cancel</button>
              <button onClick={createIssue} disabled={!newIssue.title} className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">Save</button>
            </div>
          </div>
        </div>
      )}

      {/* Risk form modal */}
      {showRiskForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-base font-semibold">Log Risk</h2>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Title</label>
                <input value={newRisk.title} onChange={(e) => setNewRisk((f) => ({ ...f, title: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm" placeholder="Describe the risk" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-ink-muted">Likelihood</label>
                  <select value={newRisk.likelihood} onChange={(e) => setNewRisk((f) => ({ ...f, likelihood: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm">
                    {["low","medium","high"].map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-ink-muted">Impact</label>
                  <select value={newRisk.impact} onChange={(e) => setNewRisk((f) => ({ ...f, impact: e.target.value }))} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm">
                    {["low","medium","high"].map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">Mitigation Plan</label>
                <textarea value={newRisk.mitigation_plan} onChange={(e) => setNewRisk((f) => ({ ...f, mitigation_plan: e.target.value }))} rows={3} className="w-full rounded-md border border-line px-2.5 py-1.5 text-sm" placeholder="How will you mitigate this risk?" />
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setShowRiskForm(false)} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium">Cancel</button>
              <button onClick={createRisk} disabled={!newRisk.title} className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">Save</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
