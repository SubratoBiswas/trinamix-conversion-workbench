import React, { useCallback, useEffect, useState } from "react";
import { Search, Plus, Plug, RefreshCw, CheckCircle, XCircle, ChevronRight, Database, Loader2 } from "lucide-react";
import { DiscoveryApi } from "@/api";
import { Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

type Connection = {
  id: string;
  name: string;
  system_type: string;
  host?: string;
  username?: string;
  account_id?: string;
  base_url?: string;
  last_test_ok?: boolean;
  last_tested_at?: string;
  last_test_error?: string;
  created_at: string;
};

type DiscoveryRun = {
  id: string;
  connection_id: string;
  status: string;
  modules_requested: string[];
  objects_found: number;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
  created_at: string;
};

type DiscoveredObject = {
  id: string;
  module?: string;
  object_name: string;
  object_type: string;
  row_count?: number;
  column_count?: number;
  suggested_fbdi_object?: string;
  suggestion_confidence: number;
  selected: boolean;
};

const SYSTEM_TYPES = ["netsuite", "oracle_ebs", "sap", "dynamics", "manual"];
const ALL_MODULES = ["GL", "AP", "AR", "INV", "PO", "HR", "FA"];

const SYSTEM_LABELS: Record<string, string> = {
  netsuite: "NetSuite",
  oracle_ebs: "Oracle E-Business Suite",
  sap: "SAP",
  dynamics: "Microsoft Dynamics",
  manual: "Manual / CSV",
};

export const DiscoveryPage: React.FC = () => {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [selectedConn, setSelectedConn] = useState<Connection | null>(null);
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<DiscoveryRun | null>(null);
  const [objects, setObjects] = useState<DiscoveredObject[]>([]);
  const [scanning, setScanning] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [selectedModules, setSelectedModules] = useState<string[]>([]);

  // New connection form state
  const [form, setForm] = useState({
    system_type: "netsuite",
    name: "",
    host: "",
    port: "",
    service_name: "",
    username: "",
    password: "",
    account_id: "",
    base_url: "",
  });

  const loadConnections = useCallback(async () => {
    try {
      const data = await DiscoveryApi.listConnections();
      setConnections(data);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadConnections(); }, [loadConnections]);

  const handleSelectConn = useCallback(async (conn: Connection) => {
    setSelectedConn(conn);
    setSelectedRun(null);
    setObjects([]);
    try {
      const data = await DiscoveryApi.listRuns(conn.id);
      setRuns(data);
    } catch {
      setRuns([]);
    }
  }, []);

  const handleTest = useCallback(async (conn: Connection) => {
    setTesting(conn.id);
    try {
      const updated = await DiscoveryApi.testConnection(conn.id);
      setConnections((cs) => cs.map((c) => (c.id === conn.id ? updated : c)));
      if (selectedConn?.id === conn.id) setSelectedConn(updated);
    } catch {
      /* ignore */
    } finally {
      setTesting(null);
    }
  }, [selectedConn]);

  const handleStartScan = useCallback(async () => {
    if (!selectedConn) return;
    setScanning(true);
    try {
      const run: DiscoveryRun = await DiscoveryApi.startRun(selectedConn.id, selectedModules);
      setRuns((rs) => [run, ...rs]);
      setSelectedRun(run);
      const objs = await DiscoveryApi.listObjects(run.id);
      setObjects(objs);
    } catch {
      /* ignore */
    } finally {
      setScanning(false);
    }
  }, [selectedConn, selectedModules]);

  const handleSelectRun = useCallback(async (run: DiscoveryRun) => {
    setSelectedRun(run);
    try {
      const objs = await DiscoveryApi.listObjects(run.id);
      setObjects(objs);
    } catch {
      setObjects([]);
    }
  }, []);

  const toggleObject = useCallback(async (obj: DiscoveredObject) => {
    const updated = await DiscoveryApi.toggleObject(obj.id, !obj.selected);
    setObjects((os) => os.map((o) => (o.id === obj.id ? { ...o, selected: updated.selected } : o)));
  }, []);

  const handleCreateConnection = useCallback(async () => {
    try {
      const conn = await DiscoveryApi.createConnection({
        system_type: form.system_type,
        name: form.name,
        host: form.host || undefined,
        port: form.port ? parseInt(form.port) : undefined,
        service_name: form.service_name || undefined,
        username: form.username || undefined,
        password: form.password || undefined,
        account_id: form.account_id || undefined,
        base_url: form.base_url || undefined,
      });
      setConnections((cs) => [conn, ...cs]);
      setShowForm(false);
      setForm({ system_type: "netsuite", name: "", host: "", port: "", service_name: "", username: "", password: "", account_id: "", base_url: "" });
    } catch {
      /* ignore */
    }
  }, [form]);

  if (loading) return <PageLoader />;

  return (
    <>
      <PageTitle
        title="Source Discovery"
        subtitle="Connect to legacy systems and auto-discover migration objects"
        actions={
          <button
            onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark"
          >
            <Plus className="h-3.5 w-3.5" /> New Connection
          </button>
        }
      />

      <div className="flex gap-4">
        {/* Left: connection list */}
        <div className="w-64 shrink-0 space-y-2">
          {connections.length === 0 ? (
            <Card>
              <CardBody>
                <EmptyState
                  icon={<Plug className="h-5 w-5" />}
                  title="No connections"
                  description="Add a source system connection to begin discovery."
                />
              </CardBody>
            </Card>
          ) : (
            connections.map((conn) => (
              <button
                key={conn.id}
                onClick={() => handleSelectConn(conn)}
                className={cn(
                  "w-full rounded-lg border px-3 py-2.5 text-left transition",
                  selectedConn?.id === conn.id
                    ? "border-brand bg-brand-subtle"
                    : "border-line bg-white hover:border-brand-subtle"
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-ink">{conn.name}</span>
                  {conn.last_test_ok === true && <CheckCircle className="h-3.5 w-3.5 text-success" />}
                  {conn.last_test_ok === false && <XCircle className="h-3.5 w-3.5 text-danger" />}
                </div>
                <div className="mt-0.5 text-[11px] text-ink-muted">{SYSTEM_LABELS[conn.system_type] ?? conn.system_type}</div>
              </button>
            ))
          )}
        </div>

        {/* Right: detail panel */}
        <div className="flex-1 space-y-3">
          {!selectedConn ? (
            <Card>
              <CardBody>
                <EmptyState
                  icon={<Database className="h-5 w-5" />}
                  title="Select a connection"
                  description="Choose a source connection on the left to view its discovery runs and start a scan."
                />
              </CardBody>
            </Card>
          ) : (
            <>
              {/* Connection header */}
              <Card>
                <CardHeader
                  title={selectedConn.name}
                  subtitle={SYSTEM_LABELS[selectedConn.system_type] ?? selectedConn.system_type}
                  actions={
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleTest(selectedConn)}
                        disabled={testing === selectedConn.id}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2.5 py-1 text-[11px] font-medium text-ink hover:bg-canvas"
                      >
                        {testing === selectedConn.id
                          ? <Loader2 className="h-3 w-3 animate-spin" />
                          : <RefreshCw className="h-3 w-3" />}
                        Test
                      </button>
                      <button
                        onClick={handleStartScan}
                        disabled={scanning}
                        className="inline-flex items-center gap-1 rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white hover:bg-brand-dark disabled:opacity-50"
                      >
                        {scanning ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
                        {scanning ? "Scanning…" : "Run Discovery"}
                      </button>
                    </div>
                  }
                />
                <CardBody>
                  {selectedConn.last_test_ok === false && (
                    <div className="mb-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
                      Last test failed: {selectedConn.last_test_error}
                    </div>
                  )}
                  {/* Module selector */}
                  <div className="mb-3">
                    <div className="mb-1 text-[11px] font-medium text-ink-muted">Modules to scan (leave blank for all)</div>
                    <div className="flex flex-wrap gap-1">
                      {ALL_MODULES.map((m) => (
                        <button
                          key={m}
                          onClick={() => setSelectedModules((ms) =>
                            ms.includes(m) ? ms.filter((x) => x !== m) : [...ms, m]
                          )}
                          className={cn(
                            "rounded px-2 py-0.5 text-[11px] font-medium transition",
                            selectedModules.includes(m)
                              ? "bg-brand text-white"
                              : "bg-canvas text-ink-muted hover:bg-line"
                          )}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Previous runs */}
                  {runs.length > 0 && (
                    <div>
                      <div className="mb-1 text-[11px] font-medium text-ink-muted">Previous runs</div>
                      <div className="space-y-1">
                        {runs.map((run) => (
                          <button
                            key={run.id}
                            onClick={() => handleSelectRun(run)}
                            className={cn(
                              "flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-xs",
                              selectedRun?.id === run.id
                                ? "border-brand bg-brand-subtle"
                                : "border-line bg-white hover:border-brand-subtle"
                            )}
                          >
                            <span className="font-medium">{run.objects_found} objects found</span>
                            <div className="flex items-center gap-2">
                              <Pill tone={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : "warning"}>
                                {run.status}
                              </Pill>
                              <ChevronRight className="h-3 w-3 text-ink-muted" />
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </CardBody>
              </Card>

              {/* Discovered objects */}
              {selectedRun && (
                <Card>
                  <CardHeader
                    title={`Discovered Objects (${objects.length})`}
                    subtitle="Select objects to include in migration planning"
                  />
                  <CardBody>
                    {objects.length === 0 ? (
                      <div className="py-6 text-center text-sm text-ink-muted">No objects found in this run.</div>
                    ) : (
                      <div className="space-y-2">
                        {objects.map((obj) => (
                          <div
                            key={obj.id}
                            className={cn(
                              "flex items-center gap-3 rounded-md border px-3 py-2.5 transition",
                              obj.selected ? "border-brand bg-brand-subtle" : "border-line bg-white"
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={obj.selected}
                              onChange={() => toggleObject(obj)}
                              className="h-3.5 w-3.5 accent-brand"
                            />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-semibold text-ink font-mono">{obj.object_name}</span>
                                {obj.module && <Pill tone="default">{obj.module}</Pill>}
                              </div>
                              {obj.suggested_fbdi_object && (
                                <div className="mt-0.5 text-[11px] text-ink-muted">
                                  → {obj.suggested_fbdi_object}{" "}
                                  <span className="text-brand">({Math.round(obj.suggestion_confidence * 100)}%)</span>
                                </div>
                              )}
                            </div>
                            <div className="text-right text-[11px] text-ink-muted shrink-0">
                              {obj.row_count != null && <div>{obj.row_count.toLocaleString()} rows</div>}
                              {obj.column_count != null && <div>{obj.column_count} cols</div>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardBody>
                </Card>
              )}
            </>
          )}
        </div>
      </div>

      {/* New connection modal */}
      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-base font-semibold text-ink">New Source Connection</h2>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-muted">System Type</label>
                <select
                  value={form.system_type}
                  onChange={(e) => setForm((f) => ({ ...f, system_type: e.target.value }))}
                  className="w-full rounded-md border border-line px-2.5 py-1.5 text-xs"
                >
                  {SYSTEM_TYPES.map((t) => <option key={t} value={t}>{SYSTEM_LABELS[t] ?? t}</option>)}
                </select>
              </div>
              {(["name", "host", "port", "service_name", "username", "password", "account_id", "base_url"] as const).map((field) => (
                <div key={field}>
                  <label className="mb-1 block text-[11px] font-medium text-ink-muted capitalize">{field.replace(/_/g, " ")}</label>
                  <input
                    type={field === "password" ? "password" : "text"}
                    value={form[field]}
                    onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                    className="w-full rounded-md border border-line px-2.5 py-1.5 text-xs"
                    placeholder={field === "port" ? "1521" : ""}
                  />
                </div>
              ))}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-canvas">
                Cancel
              </button>
              <button
                onClick={handleCreateConnection}
                disabled={!form.name}
                className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-dark disabled:opacity-50"
              >
                Save Connection
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
