import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, Building2, Cable, CheckCircle2, Database,
  ShieldCheck, Sparkles, Lock, AlertCircle, Workflow, Layers, Boxes,
} from "lucide-react";
import { FusionModulesApi, ProjectsApi, SourceSystemsApi } from "@/api";
import {
  Button, Card, CardBody, Pill,
} from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { FusionModule, Project, SourceSystem } from "@/types";

/**
 * Four-step engagement-setup wizard. Replaces the old single-form
 * /projects/new page — same route, deeper UX. The four steps mirror the
 * questions a real implementation team asks at kickoff:
 *
 *   1. Engagement details (name / client / target environment / go-live)
 *   2. Source system (NetSuite, EBS, ...) — pins source_system on the project
 *   3. Source connection (mock-mode by default; live creds when ready)
 *   4. Review + create
 *
 * Project + initial SourceConnection are persisted in one round-trip so the
 * Project Overview has a valid Source Connection card on first render.
 */

type EngagementDetails = {
  name: string;
  client: string;
  target_environment: string;
  description: string;
  go_live_date: string | null;
  status: string;
  phase: string;
};

type ConnectionDetails = {
  display_name: string;
  endpoint: string;
  auth_type: string;
  mock_mode: boolean;
  // Per-source metadata (non-secret)
  metadata: Record<string, string>;
  // Per-source credentials (only used when mock_mode === false)
  credentials: Record<string, string>;
};

const PHASE_OPTIONS: { code: string; label: string; help: string }[] = [
  { code: "blueprint", label: "Blueprint", help: "Discovery + scoping + design sign-off" },
  { code: "own",       label: "Own",       help: "Build + SIT (mapping, transforms, validation)" },
  { code: "lift",      label: "Lift",      help: "Load (DEV / QA / UAT → cutover)" },
  { code: "thrive",    label: "Thrive",    help: "Stabilisation + hypercare" },
];

const STATUS_OPTIONS = [
  "planning", "in_progress", "ready_for_uat", "complete", "on_hold",
];

// Per-source metadata field templates. Each field becomes a labelled input
// in step 3; the value lands in connection.connection_metadata on the server.
const META_FIELDS: Record<string, { key: string; label: string; placeholder: string; required?: boolean }[]> = {
  netsuite: [
    { key: "account_id", label: "NetSuite account ID", placeholder: "TSTDRV1234567", required: true },
    { key: "edition", label: "Edition", placeholder: "OneWorld" },
    { key: "rest_base_url", label: "SuiteTalk REST base URL", placeholder: "https://{account}.suitetalk.api.netsuite.com" },
  ],
  oracle_ebs: [
    { key: "host", label: "DB host", placeholder: "ebs-prod-db.acme.internal", required: true },
    { key: "service_name", label: "Service name", placeholder: "APPS", required: true },
    { key: "instance_name", label: "Instance name", placeholder: "EBSPROD" },
    { key: "port", label: "Port", placeholder: "1521" },
  ],
  sap_ecc:  [{ key: "sap_router", label: "SAProuter string", placeholder: "/H/router/H/host" }],
  sap_s4:   [{ key: "host", label: "S/4 host", placeholder: "s4hana-prod.acme.internal" }],
  workday:  [{ key: "tenant", label: "Tenant", placeholder: "acme_prod" }],
  jde:      [{ key: "environment", label: "Environment", placeholder: "PD910" }],
  custom:   [{ key: "label", label: "Source label", placeholder: "Internal warehouse export" }],
};

// Per-auth-type credential templates. The form renders these as password
// inputs and the values are sealed by the server's encryption service.
const CRED_FIELDS: Record<string, { key: string; label: string; placeholder?: string }[]> = {
  oauth1_tba: [
    { key: "consumer_key", label: "Consumer key" },
    { key: "consumer_secret", label: "Consumer secret" },
    { key: "token_id", label: "Token ID" },
    { key: "token_secret", label: "Token secret" },
  ],
  oauth2_client_credentials: [
    { key: "client_id", label: "Client ID" },
    { key: "client_secret", label: "Client secret" },
  ],
  db_basic: [
    { key: "username", label: "Username" },
    { key: "password", label: "Password" },
  ],
  db_wallet: [
    { key: "wallet_location", label: "Wallet directory path" },
    { key: "wallet_password", label: "Wallet password" },
  ],
  mock: [],
};

const AUTH_TYPE_OPTIONS_BY_SOURCE: Record<string, string[]> = {
  netsuite:   ["mock", "oauth1_tba", "oauth2_client_credentials"],
  oracle_ebs: ["mock", "db_basic", "db_wallet"],
  sap_ecc:    ["mock", "db_basic"],
  sap_s4:     ["mock", "oauth2_client_credentials", "db_basic"],
  workday:    ["mock", "oauth2_client_credentials"],
  jde:        ["mock", "db_basic"],
  custom:     ["mock", "db_basic"],
};

export const SetupWizard: React.FC = () => {
  const nav = useNavigate();
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceSystems, setSourceSystems] = useState<SourceSystem[]>([]);
  const [fusionModules, setFusionModules] = useState<FusionModule[]>([]);
  const [details, setDetails] = useState<EngagementDetails>({
    name: "", client: "", target_environment: "Oracle Fusion SCM Cloud",
    description: "", go_live_date: null,
    status: "planning", phase: "blueprint",
  });
  const [sourceCode, setSourceCode] = useState<string>("");
  const [conn, setConn] = useState<ConnectionDetails>({
    display_name: "", endpoint: "", auth_type: "mock", mock_mode: true,
    metadata: {}, credentials: {},
  });
  const [selectedModules, setSelectedModules] = useState<string[]>([]);

  useEffect(() => {
    SourceSystemsApi.list().then(setSourceSystems).catch(() => setSourceSystems([]));
    FusionModulesApi.list().then(setFusionModules).catch(() => setFusionModules([]));
  }, []);

  // When the source flips, reset auth_type and the metadata/credential
  // sub-forms to a sensible default for that source so old field values
  // from an unrelated source don't leak forward.
  useEffect(() => {
    if (!sourceCode) return;
    const allowed = AUTH_TYPE_OPTIONS_BY_SOURCE[sourceCode] || ["mock"];
    setConn((prev) => ({
      ...prev,
      auth_type: allowed[0],
      mock_mode: allowed[0] === "mock",
      metadata: {},
      credentials: {},
      display_name: prev.display_name ||
        `${(details.client || "Client")} ${sourceSystems.find((s) => s.code === sourceCode)?.display_name || sourceCode}`,
    }));
  }, [sourceCode]);

  const canAdvance = useMemo(() => {
    if (step === 1) return Boolean(details.name.trim());
    if (step === 2) return Boolean(sourceCode);
    if (step === 3) {
      if (!conn.display_name.trim()) return false;
      // Mock mode is deterministic-fixture-driven — the responder
      // doesn't read metadata or credentials, so we don't gate the
      // wizard on them. Real-mode toggles the gate back on.
      if (conn.mock_mode) return true;
      const required = (META_FIELDS[sourceCode] || []).filter((f) => f.required);
      if (required.some((f) => !(conn.metadata[f.key] || "").trim())) return false;
      const fields = CRED_FIELDS[conn.auth_type] || [];
      if (fields.length === 0) return false;
      if (fields.some((f) => !(conn.credentials[f.key] || "").trim())) return false;
      return true;
    }
    // Step 4 (Scope) is optional — zero modules is OK; the engagement
    // can be planned without auto-creating conversions.
    return true;
  }, [step, details, sourceCode, conn]);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const payload: Partial<Project> & {
        initial_connection?: any;
        selected_modules?: string[];
      } = {
        name: details.name,
        client: details.client || undefined,
        target_environment: details.target_environment || undefined,
        description: details.description || undefined,
        go_live_date: details.go_live_date || undefined,
        status: details.status || "planning",
        source_system: sourceCode,
        phase: details.phase || "blueprint",
        initial_connection: {
          source_system: sourceCode,
          display_name: conn.display_name,
          endpoint: conn.endpoint || undefined,
          auth_type: conn.auth_type,
          connection_metadata: conn.metadata,
          credentials: conn.mock_mode ? undefined : conn.credentials,
          mock_mode: conn.mock_mode,
        },
        selected_modules: selectedModules,
      };
      const p = await ProjectsApi.create(payload as any);
      nav(`/projects/${p.id}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to create engagement");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <Stepper step={step} />

      {step === 1 && (
        <Step1Details details={details} setDetails={setDetails} />
      )}
      {step === 2 && (
        <Step2Source
          sourceSystems={sourceSystems}
          selected={sourceCode}
          onSelect={(code) => setSourceCode(code)}
        />
      )}
      {step === 3 && (
        <Step3Connection
          sourceCode={sourceCode}
          conn={conn}
          setConn={setConn}
        />
      )}
      {step === 4 && (
        <Step4Scope
          modules={fusionModules}
          sourceCode={sourceCode}
          selected={selectedModules}
          onChange={setSelectedModules}
        />
      )}
      {step === 5 && (
        <Step5Review
          details={details}
          sourceSystem={sourceSystems.find((s) => s.code === sourceCode)}
          conn={conn}
          selectedModules={selectedModules}
          allModules={fusionModules}
        />
      )}

      {error && (
        <div className="mt-4 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
          <AlertCircle className="mr-1 inline h-3 w-3" />
          {error}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <Button
          variant="secondary"
          onClick={() => (step === 1 ? nav("/projects") : setStep((s) => (s - 1) as any))}
          disabled={busy}
        >
          <ArrowLeft className="h-4 w-4" /> {step === 1 ? "Back to projects" : "Previous"}
        </Button>
        {step < 5 ? (
          <Button onClick={() => setStep((s) => (s + 1) as any)} disabled={!canAdvance}>
            Continue <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button onClick={submit} loading={busy} disabled={!canAdvance}>
            <CheckCircle2 className="h-4 w-4" /> Create engagement
          </Button>
        )}
      </div>
    </div>
  );
};

// ─────── Stepper ───────

const Stepper: React.FC<{ step: number }> = ({ step }) => {
  const steps = [
    { n: 1, label: "Details",     icon: Building2 },
    { n: 2, label: "Source",      icon: Database },
    { n: 3, label: "Connection",  icon: Cable },
    { n: 4, label: "Scope",       icon: Layers },
    { n: 5, label: "Review",      icon: CheckCircle2 },
  ];
  return (
    <ol className="mb-6 flex items-center gap-2 rounded-lg border border-line bg-white p-3">
      {steps.map((s, i) => {
        const Icon = s.icon;
        const active = step === s.n;
        const done = step > s.n;
        return (
          <React.Fragment key={s.n}>
            <li
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium",
                active && "bg-brand-subtle text-brand-dark",
                done && "text-ink",
                !active && !done && "text-ink-muted",
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded-full text-[10.5px] font-semibold",
                  active && "bg-brand text-white",
                  done && "bg-success text-white",
                  !active && !done && "bg-canvas text-ink-muted",
                )}
              >
                {done ? <CheckCircle2 className="h-3 w-3" /> : <Icon className="h-3 w-3" />}
              </span>
              {s.label}
            </li>
            {i < steps.length - 1 && (
              <span className={cn(
                "h-px flex-1",
                step > s.n ? "bg-success" : "bg-line",
              )} />
            )}
          </React.Fragment>
        );
      })}
    </ol>
  );
};

// ─────── Step 1 — engagement details ───────

const Step1Details: React.FC<{
  details: EngagementDetails;
  setDetails: (d: EngagementDetails) => void;
}> = ({ details, setDetails }) => (
  <Card>
    <CardBody>
      <SectionTitle icon={<Building2 className="h-4 w-4" />}>Engagement details</SectionTitle>
      <div className="grid grid-cols-1 gap-3">
        <Field label="Engagement name" required>
          <input
            className="input" autoFocus
            placeholder="e.g. Acme — Oracle SCM Cloud Phase 1"
            value={details.name}
            onChange={(e) => setDetails({ ...details, name: e.target.value })}
          />
        </Field>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="Client">
            <input
              className="input" placeholder="Acme Corp"
              value={details.client}
              onChange={(e) => setDetails({ ...details, client: e.target.value })}
            />
          </Field>
          <Field label="Target environment">
            <input
              className="input" placeholder="Oracle Fusion SCM Cloud"
              value={details.target_environment}
              onChange={(e) => setDetails({ ...details, target_environment: e.target.value })}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field label="Go-live date (optional)">
            <input
              type="date" className="input"
              value={details.go_live_date || ""}
              onChange={(e) => setDetails({ ...details, go_live_date: e.target.value || null })}
            />
          </Field>
          <Field label="Engagement status">
            <select
              className="input" value={details.status}
              onChange={(e) => setDetails({ ...details, status: e.target.value })}
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.replace("_", " ")}</option>
              ))}
            </select>
          </Field>
          <Field label="Phase">
            <select
              className="input" value={details.phase}
              onChange={(e) => setDetails({ ...details, phase: e.target.value })}
            >
              {PHASE_OPTIONS.map((p) => (
                <option key={p.code} value={p.code}>{p.label}</option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Description (optional)">
          <textarea
            className="input min-h-[80px]"
            placeholder="Scope notes, modules in play, special considerations…"
            value={details.description}
            onChange={(e) => setDetails({ ...details, description: e.target.value })}
          />
        </Field>
      </div>
    </CardBody>
  </Card>
);

// ─────── Step 2 — source system picker ───────

const Step2Source: React.FC<{
  sourceSystems: SourceSystem[];
  selected: string;
  onSelect: (code: string) => void;
}> = ({ sourceSystems, selected, onSelect }) => (
  <Card>
    <CardBody>
      <SectionTitle icon={<Database className="h-4 w-4" />}>
        Source system to migrate from
      </SectionTitle>
      <p className="mt-1 text-[12px] text-ink-muted">
        The source pins the project's Mapping Knowledge Base lookup and drives which
        discovery scanner runs. Destination is always Oracle Fusion Cloud.
      </p>

      {sourceSystems.length === 0 ? (
        <div className="mt-4 text-xs text-ink-muted">Loading source catalog…</div>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {sourceSystems.map((s) => {
            const active = s.code === selected;
            return (
              <button
                key={s.code}
                onClick={() => onSelect(s.code)}
                className={cn(
                  "group flex flex-col items-start gap-1.5 rounded-md border bg-white px-3 py-3 text-left transition",
                  active
                    ? "border-brand ring-2 ring-brand/15"
                    : "border-line hover:border-brand-dark/40 hover:shadow-soft",
                )}
              >
                <div className="flex w-full items-center justify-between">
                  <span className="text-sm font-semibold text-ink">{s.display_name}</span>
                  {/* Every source ships with a connection probe + discovery
                      scanner pathway. NetSuite + Oracle EBS run against
                      live cells when mock_mode=False; the rest exercise
                      the same dispatcher with deterministic fixtures so
                      a scoping conversation can happen day-one. */}
                  <Pill tone="success" className="!text-[9px]">Scanner ready</Pill>
                </div>
                <span className="text-[10.5px] uppercase tracking-wider text-ink-muted">
                  {s.family.toUpperCase()}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </CardBody>
  </Card>
);

// ─────── Step 3 — connection ───────

const Step3Connection: React.FC<{
  sourceCode: string;
  conn: ConnectionDetails;
  setConn: (c: ConnectionDetails) => void;
}> = ({ sourceCode, conn, setConn }) => {
  const metaFields = META_FIELDS[sourceCode] || [];
  const authOptions = AUTH_TYPE_OPTIONS_BY_SOURCE[sourceCode] || ["mock"];
  const credFields = CRED_FIELDS[conn.auth_type] || [];
  return (
    <Card>
      <CardBody>
        <SectionTitle icon={<Cable className="h-4 w-4" />}>
          Source connection
        </SectionTitle>
        <p className="mt-1 text-[12px] text-ink-muted">
          Add a connection now so Discovery can scan as soon as the engagement
          is created. Default is <span className="font-semibold text-ink">mock mode</span> —
          deterministic fixtures stand in until you plug your real instance.
        </p>

        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="Display name" required>
            <input
              className="input"
              placeholder="Acme NetSuite PROD (read-only)"
              value={conn.display_name}
              onChange={(e) => setConn({ ...conn, display_name: e.target.value })}
            />
          </Field>
          <Field label="Endpoint">
            <input
              className="input"
              placeholder={sourceCode === "oracle_ebs"
                ? "ebs-prod-db.acme.internal:1521/APPS"
                : "https://acme.suitetalk.api.netsuite.com"}
              value={conn.endpoint}
              onChange={(e) => setConn({ ...conn, endpoint: e.target.value })}
            />
          </Field>
        </div>

        {metaFields.length > 0 && (
          <div className="mt-4">
            <SectionSubtitle>Source metadata</SectionSubtitle>
            <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
              {metaFields.map((f) => (
                <Field key={f.key} label={f.label} required={f.required}>
                  <input
                    className="input" placeholder={f.placeholder}
                    value={conn.metadata[f.key] || ""}
                    onChange={(e) =>
                      setConn({
                        ...conn,
                        metadata: { ...conn.metadata, [f.key]: e.target.value },
                      })
                    }
                  />
                </Field>
              ))}
            </div>
          </div>
        )}

        <div className="mt-5 rounded-md border border-line bg-canvas p-3">
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={conn.mock_mode}
              onChange={(e) => {
                const mock = e.target.checked;
                setConn({
                  ...conn,
                  mock_mode: mock,
                  auth_type: mock ? "mock" : (authOptions.find((a) => a !== "mock") || "mock"),
                  credentials: mock ? {} : conn.credentials,
                });
              }}
            />
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-ink">
              <Workflow className="h-3.5 w-3.5 text-brand-dark" /> Use mock mode for v1
            </span>
          </label>
          <p className="mt-1 text-[11px] text-ink-muted">
            Mock mode ships with deterministic fixtures — realistic counts, an
            integration health table, complexity distribution — so the team
            can demo Discovery end-to-end before live credentials are available.
            Flip this off once your read-only test instance is wired in.
          </p>
        </div>

        {!conn.mock_mode && (
          <div className="mt-4">
            <SectionSubtitle>Authentication</SectionSubtitle>
            <Field label="Auth type">
              <select
                className="input" value={conn.auth_type}
                onChange={(e) =>
                  setConn({
                    ...conn,
                    auth_type: e.target.value,
                    credentials: {},
                  })
                }
              >
                {authOptions.filter((a) => a !== "mock").map((a) => (
                  <option key={a} value={a}>{a.replace(/_/g, " ")}</option>
                ))}
              </select>
            </Field>
            {credFields.length > 0 && (
              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
                {credFields.map((f) => (
                  <Field key={f.key} label={f.label} required>
                    <input
                      type="password"
                      className="input font-mono"
                      placeholder={f.placeholder}
                      autoComplete="new-password"
                      value={conn.credentials[f.key] || ""}
                      onChange={(e) =>
                        setConn({
                          ...conn,
                          credentials: { ...conn.credentials, [f.key]: e.target.value },
                        })
                      }
                    />
                  </Field>
                ))}
              </div>
            )}
            <div className="mt-3 flex items-start gap-2 rounded-md bg-info-subtle/50 px-3 py-2 text-[11px] text-ink">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" />
              <span>
                Credentials are sealed with the project's master key (Fernet)
                before they're written to disk. They are never logged, returned
                in API responses, or echoed in audit details.
              </span>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
};

// ─────── Step 4 — implementation scope (Fusion modules) ───────

const Step4Scope: React.FC<{
  modules: FusionModule[];
  sourceCode: string;
  selected: string[];
  onChange: (codes: string[]) => void;
}> = ({ modules, sourceCode, selected, onChange }) => {
  const toggle = (code: string) => {
    if (selected.includes(code)) {
      onChange(selected.filter((c) => c !== code));
    } else {
      onChange([...selected, code]);
    }
  };

  // Compute the de-duplicated set of conversions that will be created.
  const objectsToCreate = new Map<string, { label: string; planned: number; sourceHint: string }>();
  modules
    .filter((m) => selected.includes(m.code))
    .forEach((m) => {
      m.objects.forEach((o) => {
        if (!objectsToCreate.has(o.target_object)) {
          objectsToCreate.set(o.target_object, {
            label: o.label,
            planned: o.planned_load_order,
            sourceHint: o.source_extracts[sourceCode] || "—",
          });
        }
      });
    });

  return (
    <Card>
      <CardBody>
        <SectionTitle icon={<Layers className="h-4 w-4" />}>
          Implementation scope · Fusion Cloud modules
        </SectionTitle>
        <p className="mt-1 text-[12px] text-ink-muted">
          Pick the Fusion modules in scope for this engagement. The workbench
          will auto-create one planned-status conversion per canonical Fusion
          target object — pre-set with planned load order and the matching
          source extract hint for your source ERP. You can still add /
          remove conversions on the Project Overview later.
        </p>

        {modules.length === 0 ? (
          <div className="mt-4 text-xs text-ink-muted">Loading module catalog…</div>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
            {modules.map((m) => {
              const isSelected = selected.includes(m.code);
              return (
                <button
                  key={m.code}
                  onClick={() => toggle(m.code)}
                  className={cn(
                    "rounded-md border bg-white p-3 text-left transition",
                    isSelected
                      ? "border-brand ring-2 ring-brand/20"
                      : "border-line hover:border-brand-dark/40 hover:shadow-soft",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
                      <Boxes className="h-3.5 w-3.5 text-brand-dark" />
                      {m.name}
                    </span>
                    <Pill
                      tone={isSelected ? "brand" : "neutral"}
                      className="!text-[10px]"
                    >
                      {m.objects.length} object{m.objects.length === 1 ? "" : "s"}
                    </Pill>
                  </div>
                  <div className="mt-1 text-[11.5px] text-ink-muted">{m.description}</div>
                  <div className="mt-1.5 font-mono text-[10.5px] text-ink-muted">
                    {m.objects.slice(0, 5).map((o) => o.target_object).join(" · ")}
                    {m.objects.length > 5 && ` · +${m.objects.length - 5} more`}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Preview of conversions that will be auto-created */}
        {objectsToCreate.size > 0 && (
          <div className="mt-4 rounded-md border border-brand/30 bg-brand-subtle/15 p-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
              {objectsToCreate.size} conversion{objectsToCreate.size === 1 ? "" : "s"} will be auto-created
            </div>
            <table className="mt-2 w-full text-[11.5px]">
              <thead className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
                <tr><th>Object</th><th>Load order</th><th>Source extract</th></tr>
              </thead>
              <tbody>
                {[...objectsToCreate.entries()]
                  .sort((a, b) => a[1].planned - b[1].planned)
                  .map(([target, info]) => (
                    <tr key={target} className="border-t border-line/60">
                      <td className="py-1 pr-2 font-medium text-ink">{info.label}</td>
                      <td className="py-1 pr-2 font-mono text-ink-muted">{info.planned}</td>
                      <td className="py-1 font-mono text-[10.5px] text-ink-muted">{info.sourceHint}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}

        {objectsToCreate.size === 0 && (
          <div className="mt-3 rounded-md border border-dashed border-line bg-canvas px-3 py-2 text-[11.5px] text-ink-muted">
            Optional — skipping scope leaves the engagement empty. You can
            add conversions one by one from the Project Overview later.
          </div>
        )}
      </CardBody>
    </Card>
  );
};

// ─────── Step 5 — review ───────

const Step5Review: React.FC<{
  details: EngagementDetails;
  sourceSystem: SourceSystem | undefined;
  conn: ConnectionDetails;
  selectedModules: string[];
  allModules: FusionModule[];
}> = ({ details, sourceSystem, conn, selectedModules, allModules }) => {
  const scopedModules = allModules.filter((m) => selectedModules.includes(m.code));
  const uniqueObjects = new Set<string>();
  scopedModules.forEach((m) => m.objects.forEach((o) => uniqueObjects.add(o.target_object)));
  return (
    <Card>
      <CardBody>
        <SectionTitle icon={<Sparkles className="h-4 w-4" />}>Review & create</SectionTitle>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <ReviewBlock title="Engagement">
            <ReviewRow k="Name"  v={details.name} />
            <ReviewRow k="Client" v={details.client || "—"} />
            <ReviewRow k="Target" v={details.target_environment || "—"} />
            <ReviewRow k="Go-live" v={details.go_live_date || "—"} />
            <ReviewRow k="Status" v={details.status} />
            <ReviewRow k="Phase"  v={details.phase} />
          </ReviewBlock>
          <ReviewBlock title="Source system">
            <ReviewRow k="System" v={sourceSystem?.display_name || "—"} />
            <ReviewRow k="Family" v={sourceSystem?.family?.toUpperCase() || "—"} />
            <ReviewRow k="Scanner"
              v={sourceSystem?.has_scanner_v1 ? "Ready (mock)" : "Mock only for v1"} />
          </ReviewBlock>
          <ReviewBlock title="Connection">
            <ReviewRow k="Display name" v={conn.display_name} />
            <ReviewRow k="Endpoint"     v={conn.endpoint || "—"} />
            <ReviewRow k="Auth type"    v={conn.auth_type} />
            <ReviewRow k="Mode"
              v={conn.mock_mode ? "Mock (fixtures)" : "Live (sealed credentials)"} />
          </ReviewBlock>
          <ReviewBlock title="Implementation scope">
            {scopedModules.length === 0 ? (
              <div className="text-[11px] text-ink-muted">
                No modules selected — engagement created without auto-conversions.
              </div>
            ) : (
              <>
                {scopedModules.map((m) => (
                  <ReviewRow key={m.code} k={m.name} v={`${m.objects.length} object(s)`} />
                ))}
                <ReviewRow k="Auto-create"
                  v={`${uniqueObjects.size} planned conversion${uniqueObjects.size === 1 ? "" : "s"}`} />
              </>
            )}
          </ReviewBlock>
        </div>
        <div className="mt-4 inline-flex items-center gap-1.5 rounded-md bg-success-subtle/60 px-3 py-2 text-[11px] text-success">
          <ShieldCheck className="h-3.5 w-3.5" />
          Creating the engagement saves the project, the source connection,
          {uniqueObjects.size > 0 && (
            <> {uniqueObjects.size} planned conversion{uniqueObjects.size === 1 ? "" : "s"},</>
          )}
          {" "}and an audit-log entry — all atomically.
        </div>
      </CardBody>
    </Card>
  );
};

// ─────── Tiny primitives kept local so this component is self-contained ───────

const SectionTitle: React.FC<{ icon: React.ReactNode; children: React.ReactNode }> = ({
  icon, children,
}) => (
  <div className="flex items-center gap-2 text-sm font-semibold text-ink">
    {icon}
    {children}
  </div>
);

const SectionSubtitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
    {children}
  </div>
);

const Field: React.FC<{
  label: string; required?: boolean; children: React.ReactNode;
}> = ({ label, required, children }) => (
  <div>
    <label className="label">
      {label}
      {required && <span className="ml-1 text-danger">*</span>}
    </label>
    {children}
  </div>
);

const ReviewBlock: React.FC<{ title: string; children: React.ReactNode }> = ({
  title, children,
}) => (
  <div className="rounded-md border border-line bg-canvas p-3">
    <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
      {title}
    </div>
    <div className="space-y-1.5">{children}</div>
  </div>
);

const ReviewRow: React.FC<{ k: string; v: string }> = ({ k, v }) => (
  <div className="flex items-baseline justify-between gap-3 text-xs">
    <span className="text-ink-muted">{k}</span>
    <span className="truncate font-mono text-ink">{v}</span>
  </div>
);
