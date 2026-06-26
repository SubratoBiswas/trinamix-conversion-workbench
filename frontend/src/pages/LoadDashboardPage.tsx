import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Play, RefreshCw, CheckCircle2, XCircle, AlertTriangle, Plug, Database, Loader2, ExternalLink, Activity } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from "recharts";
import { LoadApi, ProjectsApi, FusionApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate } from "@/lib/utils";
import type {
  Conversion,
  LoadError,
  LoadRun,
  LoadSummary,
  Project,
} from "@/types";

// Distinct error-category palette — purposeful, used to encode meaning in charts
const CAT_COLORS = ["#EF4444", "#F59E0B", "#3B82F6", "#8B5CF6", "#10B981", "#EC4899", "#64748B"];

export const LoadDashboardPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  // Project layer first — the URL carries both ``project`` and
  // ``conversion`` so deep-links survive refresh. We never default
  // ``conversion`` blindly to ``[0]`` across projects; the picker is
  // always scoped to the selected engagement.
  const [projects, setProjects] = useState<Project[]>([]);
  const [conversions, setConversions] = useState<Conversion[]>([]);
  const [projectId, setProjectId] = useState<string | null>(
    params.get("project") ?? null,
  );
  const [pid, setPid] = useState<string | null>(
    params.get("conversion") ?? null,
  );
  const [runs, setRuns] = useState<LoadRun[]>([]);
  const [summary, setSummary] = useState<LoadSummary | null>(null);
  const [errors, setErrors] = useState<LoadError[]>([]);
  const [running, setRunning] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── Oracle Fusion target connection + per-conversion interface tables ──
  const [fusionConn, setFusionConn] = useState<Awaited<ReturnType<typeof FusionApi.getConnection>> | null>(null);
  const [targets, setTargets] = useState<Awaited<ReturnType<typeof FusionApi.targets>> | null>(null);
  const [fusionOpen, setFusionOpen] = useState(false);
  const [fBaseUrl, setFBaseUrl] = useState("");
  const [fUser, setFUser] = useState("");
  const [fPass, setFPass] = useState("");
  const [fTesting, setFTesting] = useState(false);
  const [fTestMsg, setFTestMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const [fSaving, setFSaving] = useState(false);

  const refreshFusionConn = () =>
    FusionApi.getConnection().then((c) => {
      setFusionConn(c);
      setFBaseUrl(c.base_url || "");
      setFUser(c.username || "");
    }).catch(() => {});

  useEffect(() => { refreshFusionConn(); }, []);
  useEffect(() => {
    if (!pid) { setTargets(null); return; }
    FusionApi.targets(pid).then(setTargets).catch(() => setTargets(null));
  }, [pid]);

  const testFusion = async () => {
    setFTesting(true); setFTestMsg(null);
    try {
      const r = await FusionApi.testConnection({ base_url: fBaseUrl, username: fUser, password: fPass || undefined });
      setFTestMsg({ ok: r.ok, message: r.message });
    } catch (e: any) {
      setFTestMsg({ ok: false, message: e?.response?.data?.detail || "Test failed" });
    } finally { setFTesting(false); }
  };

  const saveFusion = async () => {
    setFSaving(true);
    try {
      await FusionApi.saveConnection({ base_url: fBaseUrl, username: fUser, password: fPass || undefined });
      setFPass("");
      await refreshFusionConn();
      setFusionOpen(false);
    } catch (e: any) {
      setFTestMsg({ ok: false, message: e?.response?.data?.detail || "Save failed" });
    } finally { setFSaving(false); }
  };

  // Load projects once; default the engagement if not URL-pinned.
  useEffect(() => {
    ProjectsApi.list().then((rows) => {
      setProjects(rows);
      if (!projectId && rows[0]) setProjectId(rows[0].id);
    });
  }, []);

  // Load this project's conversions; default conversion if not pinned
  // or if the pinned conversion belongs to a different project.
  useEffect(() => {
    if (!projectId) { setConversions([]); return; }
    ProjectsApi.conversions(projectId).then((rows) => {
      setConversions(rows);
      const pinnedBelongsToProject = !!rows.find((c) => c.id === pid);
      if (!pinnedBelongsToProject) {
        const first = rows[0];
        setPid(first ? first.id : null);
        if (first) setParams({ project: String(projectId), conversion: String(first.id) });
      } else {
        setParams({ project: String(projectId), conversion: String(pid) });
      }
    });
  }, [projectId]);

  const refresh = async () => {
    if (!pid) { setSummary(null); setRuns([]); setErrors([]); return; }
    setSummary(null); setRuns([]); setErrors([]);
    const [rs, sm] = await Promise.all([
      LoadApi.runs(pid),
      LoadApi.summary(pid).catch(() => null),
    ]);
    setRuns(rs);
    setSummary(sm);
    if (rs[0]) setErrors(await LoadApi.errors(rs[0].id));
  };
  useEffect(() => { refresh(); }, [pid]);

  const loadToFusion = async () => {
    if (!pid) return;
    // Require a configured Fusion connection — open the credentials modal first.
    if (!fusionConn?.has_credentials || !fusionConn?.base_url) {
      setFusionOpen(true);
      return;
    }
    setRunning(true);
    setLoadError(null);
    try {
      const res = await FusionApi.load(pid);
      if (!res.ok) {
        setLoadError(`${res.message}${res.status ? ` (HTTP ${res.status})` : ""}`);
      }
      await refresh();
      window.dispatchEvent(new Event("workbench:refresh"));  // status may have flipped to loaded
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || "Load to Fusion failed.";
      setLoadError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setRunning(false);
    }
  };

  // Per-run live Fusion status (keyed by load-run id).
  type RunStatus = { loading: boolean; state?: string; message?: string; raw?: string | null };
  const [statusByRun, setStatusByRun] = useState<Record<string, RunStatus>>({});

  const checkStatus = async (runId: string) => {
    setStatusByRun((m) => ({ ...m, [runId]: { ...m[runId], loading: true } }));
    try {
      const r = await FusionApi.loadStatus(runId);
      setStatusByRun((m) => ({ ...m, [runId]: { loading: false, state: r.state, message: r.message, raw: r.raw } }));
      // A polled terminal state can flip the run's status — refresh KPIs.
      if (r.state === "succeeded" || r.state === "warning" || r.state === "error") refresh();
    } catch (e: any) {
      setStatusByRun((m) => ({ ...m, [runId]: { loading: false, state: "unknown", message: e?.response?.data?.detail || "Status check failed" } }));
    }
  };

  const project = projects.find((p) => p.id === projectId) || null;
  const conversion = conversions.find((c) => c.id === pid) || null;

  // Most recent Fusion submission for this conversion — drives the success summary.
  const latestFusion = (runs.find((r) => (r as any).run_type === "fusion") as any) || null;

  const passFailData = useMemo(() => summary ? [
    { name: "Passed", value: summary.passed_count, color: "#10B981" },
    { name: "Warnings", value: summary.warning_count, color: "#F59E0B" },
    { name: "Failed", value: summary.failed_count, color: "#EF4444" },
  ] : [], [summary]);

  return (
    <>
      <ProcessingOverlay show={running} label="Submitting to Oracle Fusion…" />
      <PageTitle
        title="Load Management"
        subtitle="Run Fusion loads per engagement and inspect failures by category & root cause"
        right={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setFusionOpen(true)}
              className="btn-ghost"
              title="Configure the Oracle Fusion Cloud connection"
            >
              <Plug className={`h-4 w-4 ${fusionConn?.last_test_ok ? "text-success" : "text-ink-muted"}`} />
              {fusionConn?.has_credentials
                ? (fusionConn.last_test_ok ? "Fusion connected" : "Fusion configured")
                : "Configure Fusion"}
            </button>
            <Button onClick={loadToFusion} loading={running} disabled={!pid}>
              <Play className="h-4 w-4" /> Load to Fusion
            </Button>
          </div>
        }
      />

      <Card className="mb-4">
        <CardBody className="!py-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="label !mb-0">Engagement</label>
            <select
              className="input !w-auto min-w-[260px]"
              value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value || null)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.client ? ` · ${p.client}` : ""}
                </option>
              ))}
            </select>
            <label className="label !mb-0 ml-2">Object</label>
            <select
              className="input !w-auto min-w-[260px]"
              value={pid ?? ""}
              onChange={(e) => {
                const v = e.target.value || null;
                setPid(v);
                setParams({ project: String(projectId || ""), conversion: String(v ?? "") });
              }}
            >
              {conversions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.target_object})
                </option>
              ))}
            </select>
            <Button variant="secondary" onClick={refresh} disabled={!pid}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            {project && (
              <span className="ml-auto text-[11px] text-ink-muted">
                Source: <span className="font-mono text-ink">{project.source_system || "—"}</span>
                {Array.isArray(project.selected_modules) && project.selected_modules.length > 0 && (
                  <> · Scope: <span className="text-ink">{project.selected_modules.join(", ")}</span></>
                )}
              </span>
            )}
          </div>
        </CardBody>
      </Card>

      {/* Which Fusion FBDI interface tables this conversion will update */}
      {conversion && targets && (
        <Card className="mb-4">
          <CardBody className="!py-3">
            <div className="flex flex-wrap items-center gap-2 text-[12px]">
              <Database className="h-4 w-4 shrink-0 text-brand" />
              <span className="font-semibold text-ink">Loads into Fusion:</span>
              {targets.interface_tables.length ? (
                targets.interface_tables.map((t) => (
                  <span key={t} className="rounded border border-line bg-canvas px-2 py-0.5 font-mono text-[11px] text-ink-muted">{t}</span>
                ))
              ) : (
                <span className="italic text-ink-muted">No FBDI interface tables mapped for {targets.business_object || "this object"}</span>
              )}
              {!targets.loadable && (
                <span className="ml-1 text-[11px] text-warning">· no import job mapped yet — can't load this object</span>
              )}
              {targets.pod_url && (
                <a
                  href={targets.pod_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-auto inline-flex items-center gap-1 rounded border border-line bg-white px-2 py-0.5 text-[11px] font-medium text-brand hover:bg-brand-subtle"
                  title={targets.work_area ? `Verify in Fusion: ${targets.work_area}` : "Open Oracle Fusion"}
                >
                  <ExternalLink className="h-3 w-3" /> View in Fusion
                </a>
              )}
            </div>
            {targets.work_area && (
              <div className="mt-1.5 pl-6 text-[11px] text-ink-muted">
                After loading, verify the records in Fusion under <span className="font-medium text-ink">{targets.work_area}</span> — and check the import job in <span className="font-medium text-ink">Tools → Scheduled Processes</span>.
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {loadError && (
        <div className="mb-4 rounded-md border border-danger/40 bg-danger-subtle/50 px-4 py-3 text-[12.5px] text-danger">
          <strong>Load failed:</strong> {loadError}
        </div>
      )}

      {/* Load success / result summary — what was loaded, where to verify it. */}
      {latestFusion && (() => {
        const bo = latestFusion.business_object || targets?.business_object;
        const tables: string[] = (latestFusion.fusion_tables && latestFusion.fusion_tables.length)
          ? latestFusion.fusion_tables : (targets?.interface_tables || []);
        const reqId: string | undefined = latestFusion.fusion_request_id;
        const validReq = !!reqId && reqId !== "-1" && reqId !== "0";
        // A -1 request id means the job was never queued — show it as failed even
        // if an older run stored status "completed" from the HTTP-200 response.
        const ok = latestFusion.status === "completed" && reqId !== "-1" && reqId !== "0";
        const rawResp: string | undefined = latestFusion.fusion_response || undefined;
        const workArea: string | undefined = latestFusion.fusion_work_area || targets?.work_area || undefined;
        const podUrl: string | undefined = targets?.pod_url || fusionConn?.base_url || undefined;
        const live = statusByRun[latestFusion.id];
        return (
          <Card className={`mb-4 ${ok ? "border-success/40" : "border-danger/40"}`}>
            <CardBody className="!py-3">
              <div className="flex items-start gap-3">
                {ok ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" />
                    : <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-danger" />}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-ink">
                    {ok ? "Submitted to Oracle Fusion" : "Last Fusion load failed"} · {latestFusion.total_records} record{latestFusion.total_records === 1 ? "" : "s"}{bo ? ` of ${bo}` : ""}
                    <span className="ml-2 text-[11px] font-normal text-ink-muted">{formatDate(latestFusion.started_at)}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="text-ink-muted">Interface tables updated:</span>
                    {tables.length ? tables.map((t) => (
                      <span key={t} className="rounded border border-line bg-canvas px-2 py-0.5 font-mono text-ink-muted">{t}</span>
                    )) : <span className="italic text-ink-muted">—</span>}
                  </div>
                  {validReq ? (
                    <div className="mt-1.5 text-[11px] text-ink-muted">
                      Request ID <span className="font-mono text-ink">{reqId}</span> · Verify the records under <span className="font-medium text-ink">{workArea || "the object's work area"}</span>, and the import job in <span className="font-medium text-ink">Tools → Scheduled Processes</span>.
                    </div>
                  ) : (
                    <div className="mt-1.5 rounded-md border border-warning/40 bg-warning-subtle/50 px-2.5 py-1.5 text-[11px] text-warning-dark">
                      Oracle returned request id <span className="font-mono">{reqId || "none"}</span> — the import job was <strong>not queued</strong>, so nothing was loaded. Usually the UCM document account or ESS import job isn't available to this user/pod, or the user lacks ERP Integration / SCM privileges.
                    </div>
                  )}
                  {rawResp && (
                    <div className="mt-1 break-all font-mono text-[10.5px] text-ink-subtle" title={rawResp}>Oracle response: {rawResp.slice(0, 180)}</div>
                  )}
                  {live?.state && (
                    <div className="mt-2 flex items-center gap-2 text-[12px]">
                      <span className="text-ink-muted">Live Fusion job status:</span>
                      <StatePill state={live.state} />
                      {live.message && <span className="truncate text-[11px] text-ink-muted" title={live.message}>{live.message}</span>}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  {podUrl && (
                    <a href={podUrl} target="_blank" rel="noopener noreferrer" className="btn-ghost" title={workArea ? `Verify in Fusion: ${workArea}` : "Open Oracle Fusion"}>
                      <ExternalLink className="h-3.5 w-3.5" /> Open Fusion
                    </a>
                  )}
                  {reqId && (
                    <Button variant="secondary" onClick={() => checkStatus(latestFusion.id)} loading={live?.loading}>
                      <Activity className="h-3.5 w-3.5" /> Check status
                    </Button>
                  )}
                </div>
              </div>
            </CardBody>
          </Card>
        );
      })()}

      {/* Oracle Fusion connection modal */}
      <Modal
        open={fusionOpen}
        onClose={() => setFusionOpen(false)}
        title="Oracle Fusion Cloud connection"
        size="md"
        footer={
          <div className="flex w-full items-center justify-between">
            <Button variant="secondary" onClick={testFusion} loading={fTesting} disabled={!fBaseUrl || !fUser}>
              <Plug className="h-3.5 w-3.5" /> Test Connection
            </Button>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setFusionOpen(false)}>Cancel</Button>
              <Button onClick={saveFusion} loading={fSaving} disabled={!fBaseUrl || !fUser}>Save</Button>
            </div>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">Fusion URL</label>
            <input className="input" placeholder="https://your-pod.oraclepdemos.com"
              value={fBaseUrl} onChange={(e) => setFBaseUrl(e.target.value)} />
            {/* Guard against pasting the launchpad / SSO link instead of the REST host. */}
            {/(fa-launchpad|\?params=)/i.test(fBaseUrl) ? (
              <p className="mt-1 text-[11px] text-warning-dark">
                That looks like the Fusion <strong>launchpad / SSO link</strong>, not the application host.
                Log in through it, then copy the base URL from your browser (e.g.
                <span className="font-mono"> https://&lt;pod&gt;.oraclepdemos.com</span>) — drop any <span className="font-mono">/?params=…</span>.
              </p>
            ) : (
              <p className="mt-1 text-[11px] text-ink-muted">
                The Fusion Apps host the loader calls (<span className="font-mono">/fscmRestApi</span>, <span className="font-mono">/erpintegrations</span>) — the address bar after you sign in, not the launchpad link.
              </p>
            )}
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">Service username</label>
            <input className="input" placeholder="INTEGRATION_USER"
              value={fUser} onChange={(e) => setFUser(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              {fusionConn?.has_credentials ? "Password (leave blank to keep current)" : "Password"}
            </label>
            <input type="password" className="input" autoComplete="new-password"
              value={fPass} onChange={(e) => setFPass(e.target.value)} />
          </div>
          {fTestMsg && (
            <div className={`flex items-center gap-1.5 text-[12px] ${fTestMsg.ok ? "text-success" : "text-danger"}`}>
              {fTestMsg.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
              {fTestMsg.message}
            </div>
          )}
          <p className="text-[11px] text-ink-muted">
            Loads use Oracle's ERP Integration Service (importBulkData) — the converted FBDI file is zipped, staged to UCM, and the import job is submitted. Credentials are stored on your backend.
          </p>
        </div>
      </Modal>

      {!conversion ? (
        <Card>
          <CardBody><EmptyState
            title="Pick an engagement to begin"
            description="Load Management runs in the context of one engagement at a time. Each engagement has its own conversion list."
          /></CardBody>
        </Card>
      ) : (runs.length === 0) ? (
        <Card>
          <CardBody><EmptyState
            title="No load runs yet"
            description="Click Load to Fusion to run validation through the load engine and see pass/fail metrics."
            action={<Button onClick={loadToFusion} loading={running}><Play className="h-4 w-4" /> Load to Fusion</Button>}
          /></CardBody>
        </Card>
      ) : (
        <>
          {/* Top KPI strip */}
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <KpiBadge label="Total Records" value={summary?.total_records ?? 0} />
            <KpiBadge label="Passed" value={summary?.passed_count ?? 0} icon={CheckCircle2} tone="success" />
            <KpiBadge label="Failed" value={summary?.failed_count ?? 0} icon={XCircle} tone="danger" />
            <KpiBadge label="Warnings" value={summary?.warning_count ?? 0} icon={AlertTriangle} tone="warning" />
          </div>

          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* Pass / fail chart */}
            <Card>
              <CardHeader title="Pass / Fail Distribution" />
              <CardBody>
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={passFailData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
                      {passFailData.map((d, i) => <Cell key={i} fill={d.color} />)}
                    </Pie>
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </CardBody>
            </Card>

            {/* Error categories chart */}
            <Card className="lg:col-span-2">
              <CardHeader title="Error Categories" subtitle="Distribution of failures by category" />
              <CardBody>
                {(summary?.error_categories ?? []).length === 0 ? (
                  <EmptyState title="No errors" description="All records passed validation." />
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={summary?.error_categories ?? []} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 160 }}>
                      <CartesianGrid stroke="#F1F5F9" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} stroke="#94A3B8" fontSize={11} />
                      <YAxis type="category" dataKey="name" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} width={150} />
                      <Tooltip contentStyle={{ fontSize: 12 }} />
                      <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                        {summary.error_categories.map((_, i) => <Cell key={i} fill={CAT_COLORS[i % CAT_COLORS.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </CardBody>
            </Card>
          </div>

          {/* Root causes + dependencies */}
          <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Root Causes" subtitle={`${summary?.root_causes?.length ?? 0} unique cause(s)`} />
              {(summary?.root_causes?.length ?? 0) === 0 ? <CardBody><EmptyState title="No causes recorded" /></CardBody> :
                <table className="table-shell">
                  <thead><tr><th>Cause</th><th className="text-right">Count</th></tr></thead>
                  <tbody>
                    {(summary?.root_causes ?? []).map((c, i) => (
                      <tr key={i}>
                        <td className="text-ink">{c.cause}</td>
                        <td className="text-right tabular-nums text-ink-muted">{c.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            </Card>
            <Card>
              <CardHeader title="Dependency Impact" subtitle="Upstream objects driving these failures" />
              {(summary?.dependency_impacts?.length ?? 0) === 0 ? <CardBody><EmptyState title="No dependency impacts" /></CardBody> :
                <table className="table-shell">
                  <thead><tr><th>Object</th><th className="text-right">Impacted</th></tr></thead>
                  <tbody>
                    {(summary?.dependency_impacts ?? []).map((d, i) => (
                      <tr key={i}>
                        <td><Pill tone="warning">{d.object}</Pill></td>
                        <td className="text-right tabular-nums text-ink-muted">{d.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            </Card>
          </div>

          {/* Fusion load runs + live import status */}
          <Card className="mb-4">
            <CardHeader title="Load Runs" subtitle="Submitted Fusion loads — poll Oracle for the live import status" />
            <div className="overflow-x-auto">
              <table className="table-shell">
                <thead>
                  <tr>
                    <th>When</th><th>Type</th><th className="text-right">Records</th>
                    <th>Submission</th><th>Fusion job status</th><th>Request ID</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const isFusion = (r as any).run_type === "fusion";
                    const reqId = (r as any).fusion_request_id as string | undefined;
                    const live = statusByRun[r.id];
                    const polled = live?.state || (r as any).fusion_state;
                    return (
                      <tr key={r.id}>
                        <td className="text-ink-muted">{formatDate(r.started_at)}</td>
                        <td><Pill tone={isFusion ? "info" : "neutral"}>{(r as any).run_type || "simulate"}</Pill></td>
                        <td className="text-right tabular-nums">{r.total_records}</td>
                        <td><Pill tone={r.status === "completed" ? "success" : r.status === "failed" ? "danger" : "warning"}>{r.status}</Pill></td>
                        <td>
                          {polled ? <StatePill state={polled} /> : <span className="text-[11px] text-ink-subtle">—</span>}
                          {live?.message && (
                            <div className="max-w-[260px] truncate text-[10.5px] text-ink-muted" title={live.message}>{live.message}</div>
                          )}
                        </td>
                        <td className="font-mono text-[11px] text-ink-muted">{reqId || "—"}</td>
                        <td className="text-right">
                          {isFusion && reqId ? (
                            <button
                              onClick={() => checkStatus(r.id)}
                              disabled={live?.loading}
                              className="inline-flex items-center gap-1 rounded border border-line bg-white px-2 py-1 text-[11px] font-medium hover:bg-canvas disabled:opacity-50"
                            >
                              {live?.loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Activity className="h-3 w-3" />}
                              Check status
                            </button>
                          ) : (
                            <span className="text-[11px] text-ink-subtle">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Error grid */}
          <Card>
            <CardHeader title="Latest Run Errors" subtitle={runs[0] ? `Run #${runs[0].id} · ${formatDate(runs[0].started_at)}` : "—"} />
            {errors.length === 0 ? <CardBody><EmptyState title="No errors recorded" /></CardBody> : (
              <div className="overflow-x-auto">
                <table className="table-shell">
                  <thead>
                    <tr>
                      <th>Row</th><th>Field</th><th>Category</th>
                      <th>Message</th><th>Root Cause</th>
                      <th>Dependency</th><th>Suggested Fix</th>
                    </tr>
                  </thead>
                  <tbody>
                    {errors.slice(0, 200).map(e => (
                      <tr key={e.id}>
                        <td className="text-ink-muted">{e.row_number ?? "—"}</td>
                        <td className="font-medium">{e.object_name || "—"}</td>
                        <td><Pill tone="danger">{e.error_category}</Pill></td>
                        <td className="max-w-[320px] truncate" title={e.error_message || ""}>{e.error_message || "—"}</td>
                        <td className="max-w-[280px] truncate text-ink-muted">{e.root_cause || "—"}</td>
                        <td className="text-ink-muted">{e.related_dependency || "—"}</td>
                        <td className="max-w-[280px] truncate text-ink-muted">{e.suggested_fix || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
};

// Full-screen "the tool is working" indicator — a spinning ring shown while a
// load is being submitted to Oracle Fusion (and any other long async action).
const ProcessingOverlay: React.FC<{ show: boolean; label?: string }> = ({ show, label }) =>
  !show ? null : (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-4 rounded-2xl bg-white px-10 py-8 shadow-xl">
        <div className="relative h-14 w-14">
          <div className="absolute inset-0 rounded-full border-4 border-brand/15" />
          <div className="absolute inset-0 animate-spin rounded-full border-4 border-transparent border-t-brand border-r-brand" />
        </div>
        <div className="text-sm font-semibold text-ink">{label || "Processing…"}</div>
        <div className="text-[11px] text-ink-muted">Please wait — talking to Oracle Fusion</div>
      </div>
    </div>
  );

// Normalized Fusion ESS phase → coloured pill.
const StatePill: React.FC<{ state: string }> = ({ state }) => {
  const map: Record<string, { tone: "success" | "warning" | "danger" | "info" | "neutral"; label: string }> = {
    succeeded: { tone: "success", label: "Succeeded" },
    warning: { tone: "warning", label: "Warning" },
    error: { tone: "danger", label: "Error" },
    running: { tone: "info", label: "Running" },
    unknown: { tone: "neutral", label: "Unknown" },
  };
  const m = map[state] || map.unknown;
  return <Pill tone={m.tone}>{m.label}</Pill>;
};

const KpiBadge: React.FC<{ label: string; value: number; icon?: React.ElementType; tone?: "success" | "danger" | "warning" }> =
  ({ label, value, icon: Icon, tone }) => {
    const text = tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-ink";
    return (
      <div className="card p-3">
        <div className="flex items-center justify-between">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">{label}</div>
          {Icon && <Icon className={`h-4 w-4 ${text}`} />}
        </div>
        <div className={`mt-1 text-2xl font-semibold tabular-nums ${text}`}>{value}</div>
      </div>
    );
};
