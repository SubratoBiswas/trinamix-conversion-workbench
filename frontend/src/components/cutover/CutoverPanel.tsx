import React, { useEffect, useMemo, useState } from "react";
import {
  ShieldCheck, AlertTriangle, CheckCircle2, CircleAlert, Clock,
  ListChecks, FileSignature, Bug, AlertOctagon, Activity,
  ArrowUpRight, Plus, X, Loader2, RefreshCw, ChevronDown, ChevronRight,
} from "lucide-react";
import { Slice6Api } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type {
  DressRehearsal, Issue, ReconciliationCheck, Risk, RunbookTask,
  Safeguard, SafeguardsResponse, SignOff,
} from "@/types";

/**
 * CutoverPanel — embedded on Project Overview.
 *
 * Owns the Slice 6 surfaces: Safeguards strip, Cutover Runbook, Issues,
 * Risks, Dress Rehearsals, Sign-off ledger, and the Reconciliation
 * checks table. Designed as a single tabbed panel rather than seven
 * separate cards so the Project Overview stays readable.
 */

type Tab = "safeguards" | "runbook" | "recon" | "issues" | "risks" | "rehearsals" | "signoffs";

const SAFEGUARD_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  pass: "success",
  warning: "warning",
  fail: "danger",
  not_run: "neutral",
};

const SAFEGUARD_LABEL: Record<string, string> = {
  pass: "PASS",
  warning: "WARN",
  fail: "FAIL",
  not_run: "—",
};

export const CutoverPanel: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [tab, setTab] = useState<Tab>("safeguards");
  const [safeguards, setSafeguards] = useState<SafeguardsResponse | null>(null);

  const reload = async () => {
    Slice6Api.safeguards(projectId).then(setSafeguards).catch(() => setSafeguards(null));
  };
  useEffect(() => { reload(); }, [projectId]);

  return (
    <Card className="mt-4">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4 text-brand" />
            Cutover Orchestration
          </span>
        }
        subtitle={
          safeguards
            ? `${Math.round(safeguards.pass_rate * 100)}% of 7 cutover safeguards passing`
            : "Safeguards · Runbook · Reconciliation · Issues · Risks · Rehearsals · Sign-offs"
        }
        actions={
          <button
            onClick={reload}
            className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium text-ink-muted hover:border-brand hover:text-brand-dark"
          >
            <RefreshCw className="h-3 w-3" /> Refresh
          </button>
        }
      />
      <div className="border-b border-line">
        <TabRow tab={tab} onChange={setTab} />
      </div>
      <CardBody>
        {tab === "safeguards" && <SafeguardsView data={safeguards} />}
        {tab === "runbook"    && <RunbookView projectId={projectId} />}
        {tab === "recon"      && <ReconciliationView projectId={projectId} />}
        {tab === "issues"     && <IssuesView projectId={projectId} />}
        {tab === "risks"      && <RisksView projectId={projectId} />}
        {tab === "rehearsals" && <RehearsalsView projectId={projectId} />}
        {tab === "signoffs"   && <SignOffsView projectId={projectId} />}
      </CardBody>
    </Card>
  );
};

const TabRow: React.FC<{ tab: Tab; onChange: (t: Tab) => void }> = ({ tab, onChange }) => {
  const tabs: { v: Tab; label: string; icon: React.ElementType }[] = [
    { v: "safeguards", label: "Safeguards",  icon: ShieldCheck },
    { v: "runbook",    label: "Runbook",     icon: ListChecks },
    { v: "recon",      label: "Reconciliation", icon: Activity },
    { v: "issues",     label: "Issues",      icon: Bug },
    { v: "risks",      label: "Risks",       icon: AlertOctagon },
    { v: "rehearsals", label: "Rehearsals",  icon: Clock },
    { v: "signoffs",   label: "Sign-offs",   icon: FileSignature },
  ];
  return (
    <div className="flex flex-wrap gap-1 px-4 py-2">
      {tabs.map((t) => {
        const Icon = t.icon;
        const active = tab === t.v;
        return (
          <button
            key={t.v}
            onClick={() => onChange(t.v)}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium",
              active ? "bg-brand text-white" : "text-ink-muted hover:text-ink"
            )}
          >
            <Icon className="h-3 w-3" />
            {t.label}
          </button>
        );
      })}
    </div>
  );
};

// ─────── Safeguards ───────

const SafeguardsView: React.FC<{ data: SafeguardsResponse | null }> = ({ data }) => {
  if (!data) return <Loader />;
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {data.safeguards.map((s) => <SafeguardCard key={s.code} s={s} />)}
    </div>
  );
};

const SafeguardCard: React.FC<{ s: Safeguard }> = ({ s }) => {
  const tone = SAFEGUARD_TONE[s.status] || "neutral";
  return (
    <div className={cn(
      "rounded-lg border-2 bg-white px-3 py-2.5",
      tone === "success" ? "border-success/60" :
      tone === "warning" ? "border-warning/60" :
      tone === "danger"  ? "border-danger/60"  :
      "border-line"
    )}>
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold text-ink">{s.name}</span>
        <Pill tone={tone} className="!text-[10px]">{SAFEGUARD_LABEL[s.status]}</Pill>
      </div>
      <div className="mt-1 text-[11.5px] leading-snug text-ink-muted">{s.message}</div>
    </div>
  );
};

// ─────── Runbook ───────

const RunbookView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [tasks, setTasks] = useState<RunbookTask[] | null>(null);
  const [seeding, setSeeding] = useState(false);

  const reload = () => Slice6Api.runbook(projectId).then(setTasks);
  useEffect(() => { reload(); }, [projectId]);

  const seed = async () => {
    setSeeding(true);
    try {
      const rows = await Slice6Api.seedRunbook(projectId);
      setTasks(rows);
    } finally {
      setSeeding(false);
    }
  };

  const advance = async (t: RunbookTask, next: string) => {
    const body: Partial<RunbookTask> = { status: next };
    if (next === "in_progress" && !t.started_at) body.started_at = new Date().toISOString();
    if (next === "complete") body.completed_at = new Date().toISOString();
    const updated = await Slice6Api.updateRunbookTask(t.id, body);
    setTasks((rows) => rows?.map((r) => r.id === updated.id ? updated : r) || null);
  };

  if (!tasks) return <Loader />;
  if (tasks.length === 0) return (
    <EmptyState
      icon={<ListChecks className="h-5 w-5" />}
      title="No cutover runbook yet"
      description="Seed the canonical 15-step Oracle Fusion runbook. You can assign owners, change durations, and add tasks after."
      action={<Button onClick={seed} loading={seeding}><Plus className="h-4 w-4" /> Seed runbook</Button>}
    />
  );

  const totalExpected = tasks.reduce((s, t) => s + (t.expected_duration_minutes || 0), 0);
  const completed = tasks.filter((t) => t.status === "complete").length;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between rounded-md bg-canvas px-3 py-2 text-[12px] text-ink-muted">
        <span>
          <span className="font-mono font-semibold text-ink">{completed}/{tasks.length}</span> tasks complete
          · <span className="font-mono">{Math.round(totalExpected / 60)}h {totalExpected % 60}m</span> total expected
        </span>
      </div>
      <table className="table-shell !text-[12px]">
        <thead>
          <tr>
            <th className="!w-12">#</th>
            <th>Task</th>
            <th>Phase</th>
            <th>Owner</th>
            <th>Expected</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td className="font-mono text-[11px] text-ink-muted">{Math.floor(t.sequence / 10)}</td>
              <td>
                <span className="font-medium text-ink">{t.title}</span>
                {t.severity === "critical" && (
                  <Pill tone="danger" className="ml-1.5 !text-[9px]">CRIT</Pill>
                )}
              </td>
              <td className="text-[11px] uppercase text-ink-muted">{t.phase}</td>
              <td className="text-[11px] text-ink-muted">{t.owner_email || "—"}</td>
              <td className="font-mono text-[11px] text-ink-muted">{t.expected_duration_minutes}m</td>
              <td><TaskStatusPill status={t.status} /></td>
              <td className="text-right">
                <select
                  className="input !h-7 !text-[11px]"
                  value={t.status}
                  onChange={(e) => advance(t, e.target.value)}
                >
                  <option value="pending">pending</option>
                  <option value="in_progress">in progress</option>
                  <option value="blocked">blocked</option>
                  <option value="complete">complete</option>
                  <option value="skipped">skipped</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const TaskStatusPill: React.FC<{ status: string }> = ({ status }) => {
  const tone =
    status === "complete"    ? "success" :
    status === "in_progress" ? "info" :
    status === "blocked"     ? "danger" :
    status === "skipped"     ? "neutral" : "warning";
  return <Pill tone={tone} className="!text-[10px]">{status.replace("_", " ")}</Pill>;
};

// ─────── Reconciliation ───────

const ReconciliationView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [rows, setRows] = useState<ReconciliationCheck[] | null>(null);
  const [seeding, setSeeding] = useState(false);

  const reload = () => Slice6Api.reconciliation(projectId).then(setRows);
  useEffect(() => { reload(); }, [projectId]);

  const seed = async () => {
    setSeeding(true);
    try {
      const refreshed = await Slice6Api.seedReconciliation(projectId);
      setRows(refreshed);
    } finally {
      setSeeding(false);
    }
  };

  if (!rows) return <Loader />;
  if (rows.length === 0) return (
    <EmptyState
      icon={<Activity className="h-5 w-5" />}
      title="No reconciliation checks yet"
      description="Seed plausible control-total checks for every loaded conversion. Mock-mode generates realistic variances; live mode runs your own SQL contracts."
      action={<Button onClick={seed} loading={seeding}><Plus className="h-4 w-4" /> Seed reconciliation</Button>}
    />
  );

  const totalVariance = rows
    .filter((r) => r.currency === "USD")
    .reduce((s, r) => s + Math.abs(r.variance), 0);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between rounded-md bg-canvas px-3 py-2 text-[12px] text-ink-muted">
        <span><span className="font-mono font-semibold text-ink">{rows.filter((r) => r.status === "pass").length}/{rows.length}</span> checks passing</span>
        <span>Total |variance| (USD only): <span className="font-mono font-semibold text-ink">${totalVariance.toLocaleString()}</span></span>
        <button
          onClick={seed}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-brand-dark hover:underline"
        >
          <RefreshCw className="h-3 w-3" /> Re-seed
        </button>
      </div>
      <table className="table-shell !text-[12px]">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Source</th>
            <th>Target</th>
            <th>Variance</th>
            <th>Tolerance</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="font-medium text-ink">{r.metric_name}</td>
              <td className="font-mono text-[11px] text-ink-muted">{fmtNum(r.source_value, r.currency)}</td>
              <td className="font-mono text-[11px] text-ink-muted">{fmtNum(r.target_value, r.currency)}</td>
              <td className={cn("font-mono text-[11px]",
                r.status === "pass" ? "text-success" :
                r.status === "warning" ? "text-warning" : "text-danger"
              )}>
                {fmtNum(r.variance, r.currency)} ({r.variance_pct.toFixed(2)}%)
              </td>
              <td className="font-mono text-[11px] text-ink-muted">±{r.tolerance_pct.toFixed(1)}%</td>
              <td><Pill tone={
                r.status === "pass" ? "success" :
                r.status === "warning" ? "warning" : "danger"
              } className="!text-[10px]">{r.status.toUpperCase()}</Pill></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const fmtNum = (n: number, currency?: string | null) => {
  if (currency === "USD") return `$${Math.round(n).toLocaleString()}`;
  return Math.round(n).toLocaleString();
};

// ─────── Issues ───────

const IssuesView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const reload = () => Slice6Api.issues(projectId).then(setIssues);
  useEffect(() => { reload(); }, [projectId]);

  if (!issues) return <Loader />;

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-ink-muted">
          {issues.filter((i) => i.status !== "resolved" && i.status !== "wont_fix").length} open
        </span>
        <Button onClick={() => setModalOpen(true)} className="!h-8 !text-xs">
          <Plus className="h-3.5 w-3.5" /> Raise issue
        </Button>
      </div>
      {issues.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 className="h-5 w-5" />}
          title="No issues raised"
          description="Issues raised here surface on the Migration Readiness Score, the CFO exec card, and the AI Copilot's project context."
        />
      ) : (
        <table className="table-shell !text-[12px]">
          <thead>
            <tr>
              <th>Title</th>
              <th>Severity</th>
              <th>Status</th>
              <th>Owner</th>
              <th>Due</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {issues.map((i) => (
              <tr key={i.id}>
                <td>
                  <span className="font-medium text-ink">{i.title}</span>
                  {i.description && (
                    <div className="text-[11px] text-ink-muted">{i.description}</div>
                  )}
                </td>
                <td><SeverityPill severity={i.severity} /></td>
                <td><IssueStatusPill status={i.status} /></td>
                <td className="text-[11px] text-ink-muted">{i.owner_email || "—"}</td>
                <td className="text-[11px] text-ink-muted">{i.due_date || "—"}</td>
                <td className="text-right">
                  <select
                    className="input !h-7 !text-[11px]"
                    value={i.status}
                    onChange={async (e) => {
                      await Slice6Api.updateIssue(i.id, { status: e.target.value });
                      reload();
                    }}
                  >
                    <option value="open">open</option>
                    <option value="in_progress">in progress</option>
                    <option value="blocked">blocked</option>
                    <option value="resolved">resolved</option>
                    <option value="wont_fix">won't fix</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {modalOpen && (
        <RaiseIssueModal
          projectId={projectId}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); reload(); }}
        />
      )}
    </div>
  );
};

const SeverityPill: React.FC<{ severity: string }> = ({ severity }) => {
  const tone =
    severity === "critical" ? "danger" :
    severity === "high"     ? "danger" :
    severity === "medium"   ? "warning" : "neutral";
  return <Pill tone={tone} className="!text-[10px]">{severity}</Pill>;
};

const IssueStatusPill: React.FC<{ status: string }> = ({ status }) => {
  const tone =
    status === "resolved" || status === "wont_fix" ? "success" :
    status === "blocked"                            ? "danger"  :
    status === "in_progress"                        ? "info"    : "warning";
  return <Pill tone={tone} className="!text-[10px]">{status.replace("_", " ")}</Pill>;
};

const RaiseIssueModal: React.FC<{
  projectId: number;
  onClose: () => void;
  onSaved: () => void;
}> = ({ projectId, onClose, onSaved }) => {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState("medium");
  const [owner, setOwner] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title="Raise issue" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button
        loading={busy}
        disabled={!title.trim()}
        onClick={async () => {
          setBusy(true);
          try {
            await Slice6Api.createIssue(projectId, {
              title, description: description || undefined,
              severity, owner_email: owner || undefined,
              due_date: due || undefined,
            } as any);
            onSaved();
          } finally { setBusy(false); }
        }}
      >Save</Button>
    </>}>
      <div className="space-y-3">
        <div>
          <label className="label">Title</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea className="input min-h-[80px]" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label">Severity</label>
            <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </div>
          <div>
            <label className="label">Owner email</label>
            <input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} />
          </div>
          <div>
            <label className="label">Due date</label>
            <input type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
          </div>
        </div>
      </div>
    </Modal>
  );
};

// ─────── Risks ───────

const RisksView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [risks, setRisks] = useState<Risk[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const reload = () => Slice6Api.risks(projectId).then(setRisks);
  useEffect(() => { reload(); }, [projectId]);
  if (!risks) return <Loader />;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-ink-muted">
          {risks.filter((r) => r.status !== "closed").length} open · sorted by score (probability × impact)
        </span>
        <Button onClick={() => setModalOpen(true)} className="!h-8 !text-xs">
          <Plus className="h-3.5 w-3.5" /> Add risk
        </Button>
      </div>
      {risks.length === 0 ? (
        <EmptyState
          icon={<AlertOctagon className="h-5 w-5" />}
          title="No risks in the register"
          description="Add risks the steering committee should track each week. Score = probability (1–5) × impact (1–5)."
        />
      ) : (
        <table className="table-shell !text-[12px]">
          <thead>
            <tr>
              <th>Risk</th>
              <th>Score</th>
              <th>Probability</th>
              <th>Impact</th>
              <th>Owner</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {risks.map((r) => (
              <tr key={r.id}>
                <td>
                  <span className="font-medium text-ink">{r.title}</span>
                  {r.mitigation && (
                    <div className="text-[11px] text-ink-muted">Mitigation: {r.mitigation}</div>
                  )}
                </td>
                <td>
                  <Pill tone={r.score >= 15 ? "danger" : r.score >= 9 ? "warning" : "neutral"} className="!text-[10px]">
                    {r.score}
                  </Pill>
                </td>
                <td className="font-mono text-[11px] text-ink-muted">{r.probability}/5</td>
                <td className="font-mono text-[11px] text-ink-muted">{r.impact}/5</td>
                <td className="text-[11px] text-ink-muted">{r.owner_email || "—"}</td>
                <td>
                  <select
                    className="input !h-7 !text-[11px]"
                    value={r.status}
                    onChange={async (e) => {
                      await Slice6Api.updateRisk(r.id, { status: e.target.value });
                      reload();
                    }}
                  >
                    <option value="identified">identified</option>
                    <option value="mitigating">mitigating</option>
                    <option value="accepted">accepted</option>
                    <option value="closed">closed</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {modalOpen && (
        <AddRiskModal
          projectId={projectId}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); reload(); }}
        />
      )}
    </div>
  );
};

const AddRiskModal: React.FC<{
  projectId: number;
  onClose: () => void;
  onSaved: () => void;
}> = ({ projectId, onClose, onSaved }) => {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [probability, setProbability] = useState(3);
  const [impact, setImpact] = useState(3);
  const [mitigation, setMitigation] = useState("");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title="Add risk" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button
        loading={busy}
        disabled={!title.trim()}
        onClick={async () => {
          setBusy(true);
          try {
            await Slice6Api.createRisk(projectId, {
              title, description: description || undefined,
              probability, impact,
              mitigation: mitigation || undefined,
              owner_email: owner || undefined,
            } as any);
            onSaved();
          } finally { setBusy(false); }
        }}
      >Save</Button>
    </>}>
      <div className="space-y-3">
        <div>
          <label className="label">Title</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea className="input min-h-[60px]" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Probability (1–5)</label>
            <input type="number" min={1} max={5} className="input" value={probability} onChange={(e) => setProbability(Number(e.target.value))} />
          </div>
          <div>
            <label className="label">Impact (1–5)</label>
            <input type="number" min={1} max={5} className="input" value={impact} onChange={(e) => setImpact(Number(e.target.value))} />
          </div>
        </div>
        <div>
          <label className="label">Mitigation</label>
          <textarea className="input min-h-[60px]" value={mitigation} onChange={(e) => setMitigation(e.target.value)} />
        </div>
        <div>
          <label className="label">Owner email</label>
          <input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
};

// ─────── Dress Rehearsals ───────

const RehearsalsView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [rehearsals, setRehearsals] = useState<DressRehearsal[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const reload = () => Slice6Api.dressRehearsals(projectId).then(setRehearsals);
  useEffect(() => { reload(); }, [projectId]);
  if (!rehearsals) return <Loader />;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-ink-muted">
          {rehearsals.length} rehearsal{rehearsals.length === 1 ? "" : "s"} logged · {rehearsals.filter((r) => r.result === "pass").length} passed
        </span>
        <Button onClick={() => setModalOpen(true)} className="!h-8 !text-xs">
          <Plus className="h-3.5 w-3.5" /> Log rehearsal
        </Button>
      </div>
      {rehearsals.length === 0 ? (
        <EmptyState
          icon={<Clock className="h-5 w-5" />}
          title="No dress rehearsals yet"
          description="Most Fortune 500 cutovers run 2–3 rehearsals before go-live. Each rehearsal logs result + findings + lessons learned."
        />
      ) : (
        <table className="table-shell !text-[12px]">
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>Result</th>
              <th>Duration</th>
              <th>Led by</th>
              <th>Findings</th>
            </tr>
          </thead>
          <tbody>
            {rehearsals.map((r) => (
              <tr key={r.id}>
                <td className="font-mono">{r.sequence}</td>
                <td className="text-[11px] text-ink-muted">{r.scheduled_for ? formatDate(r.scheduled_for) : "—"}</td>
                <td><Pill tone={
                  r.result === "pass" ? "success" :
                  r.result === "warning" ? "warning" :
                  r.result === "fail"    ? "danger"  : "info"
                } className="!text-[10px]">{r.result}</Pill></td>
                <td className="font-mono text-[11px] text-ink-muted">{r.duration_minutes ? `${r.duration_minutes}m` : "—"}</td>
                <td className="text-[11px] text-ink-muted">{r.led_by || "—"}</td>
                <td className="text-[11px] text-ink-muted">
                  {r.findings_json && r.findings_json.length > 0 ? `${r.findings_json.length} note(s)` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {modalOpen && (
        <LogRehearsalModal
          projectId={projectId}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); reload(); }}
        />
      )}
    </div>
  );
};

const LogRehearsalModal: React.FC<{
  projectId: number;
  onClose: () => void;
  onSaved: () => void;
}> = ({ projectId, onClose, onSaved }) => {
  const [scheduledFor, setScheduledFor] = useState(new Date().toISOString().slice(0, 16));
  const [result, setResult] = useState("pass");
  const [duration, setDuration] = useState<number | "">("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal open onClose={onClose} title="Log dress rehearsal" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button loading={busy} onClick={async () => {
        setBusy(true);
        try {
          await Slice6Api.createDressRehearsal(projectId, {
            scheduled_for: scheduledFor,
            result, summary: summary || undefined,
            duration_minutes: typeof duration === "number" ? duration : undefined,
          } as any);
          onSaved();
        } finally { setBusy(false); }
      }}>Save</Button>
    </>}>
      <div className="space-y-3">
        <div>
          <label className="label">Scheduled / start</label>
          <input type="datetime-local" className="input" value={scheduledFor} onChange={(e) => setScheduledFor(e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Result</label>
            <select className="input" value={result} onChange={(e) => setResult(e.target.value)}>
              <option value="in_progress">in progress</option>
              <option value="pass">pass</option>
              <option value="warning">warning</option>
              <option value="fail">fail</option>
            </select>
          </div>
          <div>
            <label className="label">Duration (minutes)</label>
            <input type="number" className="input" value={duration === "" ? "" : duration}
              onChange={(e) => setDuration(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
        </div>
        <div>
          <label className="label">Summary</label>
          <textarea className="input min-h-[80px]" value={summary} onChange={(e) => setSummary(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
};

// ─────── Sign-offs ───────

const SignOffsView: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [rows, setRows] = useState<SignOff[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const reload = () => Slice6Api.signOffs(projectId).then(setRows);
  useEffect(() => { reload(); }, [projectId]);
  if (!rows) return <Loader />;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-ink-muted">
          Append-only ledger — {rows.length} record{rows.length === 1 ? "" : "s"}
        </span>
        <Button onClick={() => setModalOpen(true)} className="!h-8 !text-xs">
          <Plus className="h-3.5 w-3.5" /> Capture sign-off
        </Button>
      </div>
      {rows.length === 0 ? (
        <EmptyState
          icon={<FileSignature className="h-5 w-5" />}
          title="No sign-offs captured"
          description="Capture phase-level / conversion-level / cutover-go sign-offs here. Each row is immutable — to revoke, add a new row referencing the prior."
        />
      ) : (
        <table className="table-shell !text-[12px]">
          <thead>
            <tr>
              <th>When</th>
              <th>Kind</th>
              <th>Subject</th>
              <th>Signer</th>
              <th>Decision</th>
              <th>Comment</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="text-[11px] text-ink-muted">{formatDate(s.created_at)}</td>
                <td><Pill tone="brand" className="!text-[10px]">{s.kind}</Pill></td>
                <td className="font-medium text-ink">{s.subject}</td>
                <td className="text-[11px] text-ink-muted">
                  {s.signer_email}<br />
                  <span className="font-mono text-[10px]">({s.signer_role})</span>
                </td>
                <td>
                  <Pill tone={s.decision === "approved" ? "success" : "danger"} className="!text-[10px]">
                    {s.decision}
                  </Pill>
                </td>
                <td className="text-[11px] text-ink-muted">{s.comment || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {modalOpen && (
        <CaptureSignOffModal
          projectId={projectId}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); reload(); }}
        />
      )}
    </div>
  );
};

const CaptureSignOffModal: React.FC<{
  projectId: number;
  onClose: () => void;
  onSaved: () => void;
}> = ({ projectId, onClose, onSaved }) => {
  const [kind, setKind] = useState("phase");
  const [subject, setSubject] = useState("");
  const [signer, setSigner] = useState("");
  const [role, setRole] = useState("");
  const [decision, setDecision] = useState("approved");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  // P6 — COA readiness gate. Only fetch + enforce when the user picks
  // ``cutover_go`` (the hard gate). The Save button is disabled and the
  // banner explains exactly which conversions are below threshold.
  const [coaState, setCoaState] = useState<Awaited<ReturnType<typeof Slice6Api.coaReadiness>> | null>(null);
  const [coaLoading, setCoaLoading] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  useEffect(() => {
    if (kind !== "cutover_go" || decision !== "approved") { setCoaState(null); return; }
    setCoaLoading(true);
    Slice6Api.coaReadiness(projectId)
      .then(setCoaState)
      .catch(() => setCoaState(null))
      .finally(() => setCoaLoading(false));
  }, [kind, decision, projectId]);

  const coaBlocking =
    kind === "cutover_go" && decision === "approved" && coaState && !coaState.is_ready;
  const blockedConversions = (coaState?.conversions || []).filter(
    (c) => c.has_structure && c.blocker_reason,
  );

  return (
    <Modal open onClose={onClose} title="Capture sign-off" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button
        loading={busy}
        disabled={!subject.trim() || !signer.trim() || !role.trim() || !!coaBlocking}
        onClick={async () => {
          setBusy(true);
          setSubmitError(null);
          try {
            await Slice6Api.createSignOff(projectId, {
              kind, subject, signer_email: signer, signer_role: role,
              decision, comment: comment || undefined,
            });
            onSaved();
          } catch (e: any) {
            // The COA gate returns 409 with a structured payload — surface
            // the detail.message inline so the analyst can see exactly
            // which conversion's coverage is short.
            const detail = e?.response?.data?.detail;
            const msg = typeof detail === "string"
              ? detail
              : (detail?.message || e?.message || "Sign-off failed.");
            setSubmitError(msg);
          } finally { setBusy(false); }
        }}
      >Save</Button>
    </>}>
      <div className="space-y-3">
        {/* P6 — COA readiness banner (visible only for cutover_go). */}
        {kind === "cutover_go" && decision === "approved" && (
          <div className={cn(
            "rounded-md border px-3 py-2.5 text-[12.5px]",
            coaLoading ? "border-line bg-canvas" :
            !coaState ? "border-line bg-canvas" :
            coaState.is_ready ? "border-success/40 bg-success-subtle/40" :
            "border-danger/40 bg-danger-subtle/50",
          )}>
            {coaLoading ? (
              <span className="text-ink-muted">Evaluating COA coverage…</span>
            ) : !coaState ? (
              <span className="text-ink-muted">Could not evaluate COA readiness.</span>
            ) : coaState.is_ready ? (
              <span className="text-success">
                <strong>COA gate cleared.</strong>{" "}
                {coaState.worst_coverage_pct != null
                  ? `Worst-case coverage ${coaState.worst_coverage_pct.toFixed(1)}% (threshold ${coaState.threshold_pct}%).`
                  : "No COA scope on this engagement — gate is N/A."}
              </span>
            ) : (
              <div className="text-danger">
                <div className="font-semibold">Cutover-Go is BLOCKED by COA gate.</div>
                <div className="mt-0.5 leading-snug text-ink">
                  {coaState.blocker_reason || "COA coverage below threshold."}
                </div>
                {blockedConversions.length > 0 && (
                  <ul className="mt-1.5 list-inside list-disc text-[11.5px] text-ink-muted">
                    {blockedConversions.map((c) => (
                      <li key={c.conversion_id}>
                        <span className="font-medium text-ink">{c.conversion_name}</span>
                        {": "}
                        {c.coverage_pct != null
                          ? `${c.coverage_pct.toFixed(1)}% (${c.invalid_rows}/${c.total_rows} rows failing)`
                          : (c.blocker_reason || "incomplete")}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
        {submitError && (
          <div className="rounded-md border border-danger/30 bg-danger-subtle/40 px-3 py-2 text-[12px] text-danger">
            {submitError}
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Kind</label>
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="phase">phase</option>
              <option value="conversion">conversion</option>
              <option value="coa">coa</option>
              <option value="uat">uat</option>
              <option value="cutover_go">cutover_go</option>
            </select>
          </div>
          <div>
            <label className="label">Decision</label>
            <select className="input" value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="approved">approved</option>
              <option value="rejected">rejected</option>
            </select>
          </div>
        </div>
        <div>
          <label className="label">Subject</label>
          <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)}
            placeholder="Phase 'Own' complete · Item Master signed off · …" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Signer email</label>
            <input className="input" value={signer} onChange={(e) => setSigner(e.target.value)} />
          </div>
          <div>
            <label className="label">Signer role</label>
            <input className="input" value={role} onChange={(e) => setRole(e.target.value)}
              placeholder="CFO · Data Owner · Migration Lead" />
          </div>
        </div>
        <div>
          <label className="label">Comment</label>
          <textarea className="input min-h-[60px]" value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
};

// ─────── Shared loader ───────

const Loader: React.FC = () => (
  <div className="flex items-center gap-2 text-xs text-ink-muted">
    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
  </div>
);
