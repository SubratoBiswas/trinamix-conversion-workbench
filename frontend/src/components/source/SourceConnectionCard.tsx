import React, { useEffect, useState } from "react";
import {
  Cable, CheckCircle2, AlertTriangle, Loader2, ShieldCheck, Database,
  Workflow, Plus, Clock, Lock, X,
} from "lucide-react";
import { ProjectsApi, SourceConnectionsApi, SourceSystemsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, Pill,
} from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type { SourceConnection, SourceSystem, ConnectionTestResult } from "@/types";

/**
 * Source Connection card — embedded on Project Overview.
 *
 * Shows the single (for v1) per-project connection: its status, last-test
 * result, detected metadata (subsidiary count, modules installed, ...), and
 * the probe-by-probe breakdown from the most recent test. The Test button
 * fires a real connection probe and refreshes the card inline.
 *
 * If no connection exists yet (e.g. the user skipped the Setup Wizard step),
 * the card surfaces a "+ Add connection" affordance instead of an empty
 * state — keeping the action one click away.
 */

const STATUS_PILL: Record<string, { tone: "success" | "warning" | "danger" | "neutral" | "info"; label: string }> = {
  ok:       { tone: "success", label: "Healthy" },
  degraded: { tone: "warning", label: "Degraded" },
  failed:   { tone: "danger",  label: "Failed" },
  draft:    { tone: "neutral", label: "Not tested" },
};

export const SourceConnectionCard: React.FC<{
  projectId: number;
  projectSourceSystem?: string | null;
  className?: string;
}> = ({ projectId, projectSourceSystem, className }) => {
  const [conn, setConn] = useState<SourceConnection | null | undefined>(undefined);
  const [systems, setSystems] = useState<SourceSystem[]>([]);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    const conns = await ProjectsApi.connections(projectId);
    setConn(conns[0] || null);
    setTestResult(null);
  };

  useEffect(() => {
    reload();
    SourceSystemsApi.list().then(setSystems).catch(() => setSystems([]));
  }, [projectId]);

  const onTest = async () => {
    if (!conn) return;
    setTesting(true);
    setError(null);
    try {
      const result = await SourceConnectionsApi.test(conn.id);
      setTestResult(result);
      // The connection row's status is server-updated by the test endpoint —
      // refresh it so the pill picks up the new status.
      const conns = await ProjectsApi.connections(projectId);
      setConn(conns[0] || null);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Connection test failed");
    } finally {
      setTesting(false);
    }
  };

  if (conn === undefined) {
    return (
      <Card className={className}>
        <CardHeader title="Source Connection" subtitle="Loading…" />
        <CardBody>
          <Loader2 className="h-4 w-4 animate-spin text-ink-muted" />
        </CardBody>
      </Card>
    );
  }

  if (!conn) {
    return (
      <Card className={className}>
        <CardHeader
          title={<span className="inline-flex items-center gap-1.5"><Cable className="h-4 w-4 text-brand" /> Source Connection</span>}
          subtitle="No connection configured for this engagement yet"
        />
        <CardBody>
          <div className="rounded-md border border-dashed border-line bg-canvas px-3 py-3 text-[12px] text-ink-muted">
            Add a connection so Discovery can scan customizations, integrations,
            and master-data counts. Default is mock mode — flip to live once
            your read-only test instance is ready.
          </div>
          <Button
            onClick={() => setAdding(true)}
            className="mt-3"
            disabled={!projectSourceSystem}
          >
            <Plus className="h-4 w-4" /> Add connection
          </Button>
          {!projectSourceSystem && (
            <div className="mt-2 text-[10.5px] text-ink-muted">
              Pin a source system on this engagement first (Setup Wizard step 2).
            </div>
          )}
        </CardBody>
        {adding && projectSourceSystem && (
          <AddConnectionInline
            projectId={projectId}
            sourceSystemCode={projectSourceSystem}
            sourceSystems={systems}
            onClose={() => setAdding(false)}
            onSaved={async () => { setAdding(false); await reload(); }}
          />
        )}
      </Card>
    );
  }

  const pill = STATUS_PILL[conn.status] || STATUS_PILL.draft;
  const last = conn.last_test_details;
  const sourceLabel =
    systems.find((s) => s.code === conn.source_system)?.display_name ||
    conn.source_system;

  return (
    <Card className={className}>
      <CardHeader
        title={<span className="inline-flex items-center gap-1.5"><Cable className="h-4 w-4 text-brand" /> Source Connection</span>}
        subtitle={`${sourceLabel} · ${conn.mock_mode ? "Mock mode" : "Live"}`}
        actions={<Pill tone={pill.tone}>{pill.label}</Pill>}
      />
      <CardBody>
        <div className="space-y-2.5">
          <Row k="Display name" v={conn.display_name} mono={false} />
          {conn.endpoint && <Row k="Endpoint" v={conn.endpoint} mono />}
          <Row k="Auth type" v={conn.auth_type.replace(/_/g, " ")} mono={false} />
          <Row
            k="Credentials"
            v={
              conn.has_credentials ? (
                <span className="inline-flex items-center gap-1 text-success">
                  <Lock className="h-3 w-3" /> sealed
                </span>
              ) : (
                <span className="text-ink-muted">none (mock mode)</span>
              )
            }
            mono={false}
          />
          {conn.last_test_at && (
            <Row
              k="Last tested"
              v={
                <span className="inline-flex items-center gap-1">
                  <Clock className="h-3 w-3 text-ink-muted" />
                  {formatDate(conn.last_test_at)}
                </span>
              }
              mono={false}
            />
          )}
        </div>

        {/* Detected metadata, surfaced once a test has run */}
        {last?.detected_metadata && Object.keys(last.detected_metadata).length > 0 && (
          <div className="mt-4 rounded-md border border-line bg-canvas px-3 py-2.5">
            <div className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              Detected at last probe
            </div>
            <div className="space-y-1">
              {Object.entries(last.detected_metadata)
                .slice(0, 6)
                .map(([k, v]) => (
                  <div key={k} className="flex items-baseline justify-between gap-3 text-[11px]">
                    <span className="text-ink-muted">{k}</span>
                    <span className="truncate font-mono text-ink">
                      {Array.isArray(v) ? v.join(", ") : String(v)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Per-probe table from the most recent test */}
        {(testResult || last?.probes) && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                Probes
              </span>
              {(testResult?.latency_ms || last?.latency_ms) != null && (
                <span className="font-mono text-[10.5px] text-ink-muted">
                  {testResult?.latency_ms ?? last?.latency_ms} ms total
                </span>
              )}
            </div>
            <div className="space-y-1">
              {(testResult?.probes || last?.probes || []).map((p: any, i: number) => (
                <ProbeRow key={i} probe={p} />
              ))}
            </div>
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-[11px] text-danger">
            <AlertTriangle className="mr-1 inline h-3 w-3" />
            {error}
          </div>
        )}

        <div className="mt-4 flex items-center gap-2">
          <Button onClick={onTest} loading={testing} variant="primary" className="!h-8 !text-xs">
            <Workflow className="h-3.5 w-3.5" /> Test connection
          </Button>
          {conn.mock_mode && (
            <span className="inline-flex items-center gap-1 text-[10.5px] text-ink-muted">
              <ShieldCheck className="h-3 w-3" />
              Mock fixtures — no traffic leaves this host.
            </span>
          )}
        </div>
      </CardBody>
    </Card>
  );
};

const Row: React.FC<{ k: string; v: React.ReactNode; mono?: boolean }> = ({ k, v, mono = true }) => (
  <div className="flex items-baseline justify-between gap-3 text-xs">
    <span className="text-ink-muted">{k}</span>
    <span className={cn("truncate text-ink", mono && "font-mono")}>{v}</span>
  </div>
);

const ProbeRow: React.FC<{ probe: { name: string; status: string; latency_ms?: number | null; message?: string | null } }> = ({
  probe,
}) => {
  const tone = probe.status === "ok"
    ? "text-success"
    : probe.status === "skipped"
      ? "text-ink-muted"
      : "text-danger";
  const Icon = probe.status === "ok"
    ? CheckCircle2
    : probe.status === "skipped"
      ? Database
      : AlertTriangle;
  return (
    <div className="rounded-md border border-line bg-white px-2.5 py-1.5">
      <div className="flex items-center justify-between gap-2 text-[11.5px]">
        <span className={cn("inline-flex items-center gap-1.5 font-medium", tone)}>
          <Icon className="h-3 w-3" />
          {probe.name}
        </span>
        {probe.latency_ms != null && (
          <span className="font-mono text-[10.5px] text-ink-muted">
            {probe.latency_ms} ms
          </span>
        )}
      </div>
      {probe.message && (
        <div className="mt-0.5 text-[10.5px] text-ink-muted">{probe.message}</div>
      )}
    </div>
  );
};

// ─────── Inline add-connection form (used only when no connection exists) ───────

const AddConnectionInline: React.FC<{
  projectId: number;
  sourceSystemCode: string;
  sourceSystems: SourceSystem[];
  onClose: () => void;
  onSaved: () => void;
}> = ({ projectId, sourceSystemCode, sourceSystems, onClose, onSaved }) => {
  const sys = sourceSystems.find((s) => s.code === sourceSystemCode);
  const [displayName, setDisplayName] = useState(
    sys ? `${sys.display_name} (mock)` : ""
  );
  const [endpoint, setEndpoint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await SourceConnectionsApi.create({
        project_id: projectId,
        source_system: sourceSystemCode,
        display_name: displayName.trim(),
        endpoint: endpoint.trim() || undefined,
        auth_type: "mock",
        mock_mode: true,
      });
      onSaved();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to create connection");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-line bg-canvas px-5 py-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          Add connection ({sys?.display_name})
        </span>
        <button
          onClick={onClose}
          className="rounded p-1 text-ink-muted hover:bg-white hover:text-ink"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="space-y-3">
        <div>
          <label className="label">Display name</label>
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={`${sys?.display_name || "Source"} PROD`}
          />
        </div>
        <div>
          <label className="label">Endpoint (optional)</label>
          <input
            className="input"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder={sourceSystemCode === "oracle_ebs"
              ? "ebs-prod-db.internal:1521/APPS"
              : "https://account.suitetalk.api.netsuite.com"}
          />
        </div>
        {error && (
          <div className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">{error}</div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} loading={busy} disabled={!displayName.trim()}>
            Create connection
          </Button>
        </div>
      </div>
    </div>
  );
};
