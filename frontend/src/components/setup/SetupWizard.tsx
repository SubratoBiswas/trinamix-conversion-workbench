import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, Building2, Cable, CheckCircle2, Database,
  ShieldCheck, Sparkles, Lock, AlertCircle, Workflow, Layers, Boxes,
  UploadCloud, X,
} from "lucide-react";
import { ConversionsApi, DatasetsApi, DiscoveryApi, FusionModulesApi, ProjectsApi, SourceSystemsApi } from "@/api";
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

// A source file uploaded during setup. Uploaded + AI-classified immediately on
// selection so parse errors surface right away (not silently at finish).
type WizFile = {
  key: string;
  file: File;
  status: "uploading" | "ready" | "error";
  datasetId?: string;
  templateId?: string;
  targetObject?: string;
  error?: string;
};

// One conversion object type and the ordered FBDI template steps it fans out to.
// Supplier → 6 files (Import/Address/Site/Site Assignment/Contacts/Banks);
// Customer/Item/AP/AR/GL → a single multi-sheet workbook.
type ObjType = {
  key: string;
  label: string;
  step_count: number;
  steps: { label: string; load_order: number }[];
};

// Free-text detected-target → catalog key (mirrors the backend resolver so the
// wizard can expand a detected "Supplier Import" file into the full 6-file set).
const OBJ_ALIASES: Record<string, string[]> = {
  supplier: ["supplier", "vendor"],
  customer: ["customer", "client"],
  item: ["item", "product", "material"],
  ap_invoice: ["ap invoice", "ap_invoice", "payable"],
  ar_invoice: ["ar invoice", "ar_invoice", "autoinvoice", "receivable"],
  gl_journal: ["gl journal", "gl_journal", "journal"],
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

// Default Oracle EBS connection — pre-filled when the user picks Oracle EBS.
// The user can override any field before submitting.
const EBS_DEFAULTS = {
  display_name: "Client Oracle EBS",
  endpoint: "130.61.179.1:1521/ebscdb",
  auth_type: "db_basic",
  mock_mode: false,
  metadata: {
    host: "130.61.179.1",
    service_name: "ebscdb",
    instance_name: "ebscdb",
    port: "1521",
  },
  credentials: {
    username: "apps",
    password: "apps",
  },
};

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
  // File-based source: extract files uploaded during setup. When present, the
  // engagement's conversions are created from these files (not the module catalog).
  const [fileItems, setFileItems] = useState<WizFile[]>([]);
  // Fan-out catalog (object type → ordered FBDI steps) + per-file deselected
  // step labels. A file detected as "Supplier" expands to its 6 FBDI objects,
  // all selected by default; the user can untick any before creating.
  const [objectCatalog, setObjectCatalog] = useState<ObjType[]>([]);
  const [disabledSteps, setDisabledSteps] = useState<Record<string, string[]>>({});
  const [conn, setConn] = useState<ConnectionDetails>({
    display_name: "", endpoint: "", auth_type: "mock", mock_mode: true,
    metadata: {}, credentials: {},
  });
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  // Source-kind chooser: on a brand-new engagement the user first picks how
  // source data comes in — a live DB connection (this wizard) or file upload
  // (the /convert flow). `null` = chooser not yet answered → show the modal.
  const [sourceKind, setSourceKind] = useState<null | "db" | "file">(null);

  useEffect(() => {
    SourceSystemsApi.list().then(setSourceSystems).catch(() => setSourceSystems([]));
    FusionModulesApi.list().then(setFusionModules).catch(() => setFusionModules([]));
    ConversionsApi.objectTypes().then(setObjectCatalog).catch(() => setObjectCatalog([]));
  }, []);

  // When the source flips, reset auth_type and the metadata/credential
  // sub-forms to a sensible default for that source so old field values
  // from an unrelated source don't leak forward.
  // Oracle EBS: pre-fill with real DB defaults so the team can connect
  // immediately without entering anything manually.
  useEffect(() => {
    if (!sourceCode) return;
    if (sourceCode === "oracle_ebs") {
      setConn({
        display_name: EBS_DEFAULTS.display_name,
        endpoint: EBS_DEFAULTS.endpoint,
        auth_type: EBS_DEFAULTS.auth_type,
        mock_mode: EBS_DEFAULTS.mock_mode,
        metadata: { ...EBS_DEFAULTS.metadata },
        credentials: { ...EBS_DEFAULTS.credentials },
      });
      return;
    }
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

  // File-upload path: the featured Source card pins sourceCode to "custom"
  // (filtered out of the live-source grid), so this uniquely identifies the
  // file-based engagement. No live DB connection is collected in this mode.
  const isFileMode = sourceCode === "custom";

  // Map a file's AI-detected target (e.g. "Supplier Import") to its fan-out
  // object type in the catalog, so the wizard can preview the full FBDI set.
  const resolveObjType = (detected?: string): ObjType | null => {
    if (!detected || objectCatalog.length === 0) return null;
    const d = detected.toLowerCase();
    for (const c of objectCatalog) {
      const al = OBJ_ALIASES[c.key] || [c.key];
      if (al.some((a) => d.includes(a))) return c;
    }
    return null;
  };

  const toggleStep = (fileKey: string, label: string) =>
    setDisabledSteps((prev) => {
      const cur = new Set(prev[fileKey] || []);
      cur.has(label) ? cur.delete(label) : cur.add(label);
      return { ...prev, [fileKey]: Array.from(cur) };
    });

  // Per-file summary for the Review step: the uploaded file, its resolved
  // Fusion object, and the FBDI conversion labels that will be created (after
  // any deselected fan-out steps are removed).
  const fileSummary = useMemo(
    () =>
      fileItems
        .filter((f) => f.status === "ready")
        .map((it) => {
          const ot = resolveObjType(it.targetObject);
          const steps = ot?.steps || [];
          const off = new Set(disabledSteps[it.key] || []);
          const enabled = steps.length ? steps.filter((s) => !off.has(s.label)) : [];
          return {
            key: it.key,
            fileName: it.file.name,
            objectLabel: ot?.label || it.targetObject || "Detected FBDI object",
            convLabels: steps.length
              ? enabled.map((s) => s.label)
              : [it.targetObject || it.file.name.replace(/\.[^.]+$/, "")],
          };
        }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fileItems, objectCatalog, disabledSteps],
  );

  const canAdvance = useMemo(() => {
    if (step === 1) return Boolean(details.name.trim());
    if (step === 2) return Boolean(sourceCode);
    if (step === 3) {
      // File mode: no DB connection form — allow Continue once any in-flight
      // upload has finished analyzing (files themselves are optional here).
      if (isFileMode) return !fileItems.some((f) => f.status === "uploading");
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
  }, [step, details, sourceCode, conn, isFileMode, fileItems]);

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
          display_name: conn.display_name || (isFileMode ? "Custom / Other (file upload)" : ""),
          endpoint: conn.endpoint || undefined,
          // File mode collects no live connection — persist a clean mock
          // connection so the project has a valid Source Connection card.
          auth_type: isFileMode ? "mock" : conn.auth_type,
          connection_metadata: isFileMode ? {} : conn.metadata,
          credentials: (isFileMode || conn.mock_mode) ? undefined : conn.credentials,
          mock_mode: isFileMode ? true : conn.mock_mode,
        },
        selected_modules: selectedModules,
      };
      const p = await ProjectsApi.create(payload as any);
      // File-based engagement: files were already uploaded + AI-classified on the
      // Connection step. Create one conversion per successfully-parsed file — so
      // the conversion list reflects exactly the files uploaded here. Files take
      // precedence over module auto-populate.
      const readyFiles = fileItems.filter((it) => it.status === "ready" && it.datasetId);
      if (readyFiles.length > 0) {
        // File upload always follows the FBDI template route. When a file's
        // detected target is a fan-out object (e.g. Supplier → 6 FBDI files),
        // create the full object set via generate-set, then drop any steps the
        // user deselected. Unknown object types fall back to a single conversion.
        for (const it of readyFiles) {
          const ot = resolveObjType(it.targetObject);
          if (ot) {
            const res = await ConversionsApi.generateSet({
              project_id: p.id, dataset_id: it.datasetId!, object_type: ot.key,
            }).catch(() => null);
            if (res) {
              const disabled = new Set(disabledSteps[it.key] || []);
              for (const c of res.created) {
                if (disabled.has(c.label) && c.conversion_id) {
                  await ConversionsApi.remove(c.conversion_id).catch(() => {});
                }
              }
              continue;
            }
          }
          await ConversionsApi.create({
            project_id: p.id, name: it.file.name.replace(/\.[^.]+$/, ""),
            dataset_id: it.datasetId, template_id: it.templateId, target_object: it.targetObject,
            source_type: "dataset", output_mode: "fbdi_download", status: "draft",
          } as any).catch(() => {});
        }
      } else if (selectedModules.length > 0) {
        // Catalog-based engagement: auto-create planned conversions per module.
        await ProjectsApi.autoPopulate(p.id, selectedModules);
      }
      nav(`/projects/${p.id}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to create engagement");
    } finally {
      setBusy(false);
    }
  };

  if (sourceKind === null) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
        <div className="w-full max-w-xl rounded-xl bg-white p-6 shadow-lg">
          <div className="mb-1 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-brand" />
            <h3 className="text-base font-semibold text-ink">New engagement</h3>
          </div>
          <p className="mb-4 text-[12.5px] text-ink-muted">
            How will you bring in source data for this engagement?
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              onClick={() => setSourceKind("db")}
              className="flex flex-col items-start gap-1.5 rounded-lg border border-line bg-white p-4 text-left transition hover:border-brand hover:shadow-soft"
            >
              <Cable className="h-5 w-5 text-brand" />
              <span className="text-sm font-semibold text-ink">DB connection</span>
              <span className="text-[11.5px] leading-snug text-ink-muted">
                Connect a live source system (Oracle EBS, NetSuite, …) and let Discovery scan it for objects to convert.
              </span>
            </button>
            <button
              onClick={() => nav("/convert")}
              className="flex flex-col items-start gap-1.5 rounded-lg border border-line bg-white p-4 text-left transition hover:border-brand hover:shadow-soft"
            >
              <UploadCloud className="h-5 w-5 text-brand" />
              <span className="text-sm font-semibold text-ink">File upload</span>
              <span className="text-[11.5px] leading-snug text-ink-muted">
                Upload CSV/XLSX extracts — the AI detects source &amp; target and maps each file to its Fusion FBDI template.
              </span>
            </button>
          </div>
          <div className="mt-4 text-right">
            <button
              onClick={() => nav("/projects")}
              className="text-[12px] font-medium text-ink-subtle hover:text-ink"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

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
          onSelectFileUpload={() => {
            // File-based engagement: pin the source to Custom / Other and set the
            // target to the FBDI template route so uploaded extracts export as FBDI.
            setSourceCode("custom");
            setDetails((d) => ({ ...d, target_environment: "FBDI Template (manual upload)" }));
          }}
        />
      )}
      {step === 3 && (
        <>
          {!isFileMode && (
            <Step3Connection
              sourceCode={sourceCode}
              conn={conn}
              setConn={setConn}
            />
          )}
          <FileUploadCard items={fileItems} setItems={setFileItems} sourceCode={sourceCode} fileMode={isFileMode} />
        </>
      )}
      {step === 4 && fileItems.length > 0 && (() => {
        // Total conversions that will be created = the enabled fan-out steps
        // across every ready file (a Supplier file contributes up to 6).
        const readyFiles = fileItems.filter((f) => f.status === "ready");
        const totalConvs = readyFiles.reduce((sum, it) => {
          const steps = resolveObjType(it.targetObject)?.steps || [];
          const off = new Set(disabledSteps[it.key] || []);
          return sum + (steps.length ? steps.filter((s) => !off.has(s.label)).length : 1);
        }, 0);
        return (
        <Card className="mb-4">
          <CardBody>
            <div className="mb-1 flex items-center gap-2">
              <Layers className="h-4 w-4 text-brand" />
              <span className="text-sm font-semibold text-ink">
                Conversions from your files ({totalConvs})
              </span>
            </div>
            <p className="mb-3 text-[12px] text-ink-muted">
              Each file maps to a Fusion object. Objects like <span className="font-medium text-ink">Supplier</span> load
              as a set of FBDI files (Import → Address → Site → Site Assignment → Contacts → Banks) — all selected by
              default. Untick any step you don't need; you can also change targets later on any conversion.
            </p>
            <div className="space-y-2.5">
              {fileItems.map((it) => {
                const ot = resolveObjType(it.targetObject);
                const steps = ot?.steps || [];
                const off = new Set(disabledSteps[it.key] || []);
                return (
                  <div key={it.key} className="rounded-md border border-line bg-white">
                    <div className="flex items-center gap-2 px-2.5 py-1.5 text-[12px]">
                      <Database className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
                      <span className="min-w-0 flex-1 truncate text-ink">{it.file.name}</span>
                      <ArrowRight className="h-3 w-3 shrink-0 text-ink-subtle" />
                      <span className="font-medium text-ink">{ot?.label || it.targetObject || "—"}</span>
                      {it.status === "ready" ? <Pill tone="success">FBDI</Pill>
                        : it.status === "error" ? <Pill tone="danger">error</Pill>
                        : <Pill tone="neutral">analyzing…</Pill>}
                    </div>
                    {it.status === "ready" && steps.length > 1 && (
                      <div className="border-t border-brand/25 bg-brand-subtle/15 px-2.5 py-2">
                        <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
                          <Boxes className="h-3 w-3" />
                          {steps.filter((s) => !off.has(s.label)).length} of {steps.length} {ot?.label} FBDI files selected
                        </div>
                        <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                          {steps.map((s) => {
                            const on = !off.has(s.label);
                            return (
                              <label
                                key={s.label}
                                className={cn(
                                  "flex cursor-pointer items-center gap-2 rounded border px-2 py-1 text-[11.5px] transition",
                                  on ? "border-brand/40 bg-white text-ink" : "border-line bg-canvas text-ink-muted",
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={on}
                                  onChange={() => toggleStep(it.key, s.label)}
                                  className="h-3.5 w-3.5 accent-brand"
                                />
                                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand/10 font-mono text-[9px] text-brand-dark">
                                  {s.load_order}
                                </span>
                                <span className="truncate">{s.label}</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] text-ink-subtle">
              These files are your conversions. The Fusion module catalog below is optional for a file-based engagement.
            </p>
          </CardBody>
        </Card>
        );
      })()}
      {step === 4 && (
        <Step4Scope
          modules={fusionModules}
          sourceCode={sourceCode}
          isMock={conn.mock_mode}
          liveConn={
            !conn.mock_mode && sourceCode === "oracle_ebs"
              ? {
                  host: conn.metadata.host || "",
                  port: parseInt(conn.metadata.port || "1521", 10),
                  service_name: conn.metadata.service_name || "",
                  username: conn.credentials.username || "",
                  password: conn.credentials.password || "",
                }
              : undefined
          }
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
          isFileMode={isFileMode}
          fileSummary={fileSummary}
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
            <select
              className="input"
              value={details.target_environment}
              onChange={(e) => setDetails({ ...details, target_environment: e.target.value })}
            >
              <option value="Oracle Fusion SCM Cloud">Oracle Fusion SCM Cloud</option>
              <option value="FBDI Template (manual upload)">FBDI Template (manual upload)</option>
            </select>
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
  onSelectFileUpload: () => void;
}> = ({ sourceSystems, selected, onSelect, onSelectFileUpload }) => {
  const fileActive = selected === "custom";
  // Live-source ERPs shown as the secondary path (Custom / Other is promoted to
  // the featured file-upload card above, so it's filtered out of this grid).
  const liveSystems = sourceSystems.filter((s) => s.code !== "custom");
  return (
    <Card>
      <CardBody>
        <SectionTitle icon={<Database className="h-4 w-4" />}>
          Source system to migrate from
        </SectionTitle>
        <p className="mt-1 text-[12px] text-ink-muted">
          The source pins the project's Mapping Knowledge Base lookup and drives which
          discovery scanner runs. Destination is always Oracle Fusion Cloud.
        </p>

        {/* Featured path: Custom / Other · File upload → FBDI template route */}
        <button
          onClick={onSelectFileUpload}
          className={cn(
            "mt-4 flex w-full items-start gap-3 rounded-lg border bg-white px-4 py-3.5 text-left transition",
            fileActive
              ? "border-brand ring-2 ring-brand/20"
              : "border-line hover:border-brand-dark/40 hover:shadow-soft",
          )}
        >
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-subtle text-brand-dark">
            <UploadCloud className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <span className="text-sm font-semibold text-ink">Custom / Other · File upload</span>
              <Pill tone="brand" className="!text-[9px]">FBDI template route</Pill>
            </span>
            <span className="mt-0.5 block text-[11.5px] leading-snug text-ink-muted">
              No live connection needed — upload your source extracts (CSV / XLSX) on the next step.
              Each file is auto-detected to its Oracle Fusion FBDI object and exported as an FBDI template.
            </span>
          </span>
        </button>

        {/* Secondary path: connect a live source system */}
        <div className="mt-5 mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          Or connect a live source system
        </div>
        {sourceSystems.length === 0 ? (
          <div className="text-xs text-ink-muted">Loading source catalog…</div>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {liveSystems.map((s) => {
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
};

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

/** Extract the primary (first) ALL_CAPS table name from a source-extract hint string.
 *  e.g. "Extract from MTL_SYSTEM_ITEMS_B"   → "MTL_SYSTEM_ITEMS_B"
 *       "Extract from FA_BOOKS / FA_ASSET_HISTORY" → "FA_BOOKS"
 *       "Saved Search → ..."                 → null  */
function extractTableName(hint: string): string | null {
  const m = hint.match(/\b([A-Z][A-Z0-9_]{3,})\b/);
  return m ? m[1] : null;
}

// Optional source-file upload on the Connection step. Files are uploaded and
// AI-classified immediately on selection (so parse errors surface right away),
// and drive the engagement's conversions (one per file) on finish.
const FileUploadCard: React.FC<{
  items: WizFile[];
  setItems: React.Dispatch<React.SetStateAction<WizFile[]>>;
  sourceCode: string;
  fileMode?: boolean;
}> = ({ items, setItems, sourceCode, fileMode }) => {
  const patch = (key: string, p: Partial<WizFile>) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...p } : it)));

  const add = async (fl: FileList | null) => {
    const news: WizFile[] = Array.from(fl || []).map((file) => ({
      key: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`,
      file, status: "uploading",
    }));
    if (!news.length) return;
    setItems((prev) => [...prev, ...news]);
    for (const it of news) {
      try {
        const ds: any = await DatasetsApi.upload(it.file, it.file.name.replace(/\.[^.]+$/, ""));
        const cls = await DatasetsApi.classify(ds.id).catch(() => null);
        const templateId = cls?.target?.detected_template_id || undefined;
        const targetObject = cls?.target?.suggestions?.[0]?.business_object || undefined;
        if (templateId) {
          DatasetsApi.classifyLearn(ds.id, { source_system: sourceCode, template_id: templateId, target_object: targetObject }).catch(() => {});
        }
        patch(it.key, { status: "ready", datasetId: ds.id, templateId, targetObject });
      } catch (e: any) {
        patch(it.key, { status: "error", error: e?.response?.data?.detail || "Could not read this file — use a CSV or XLSX export." });
      }
    }
  };

  return (
    <Card className="mt-4">
      <CardBody className="space-y-3">
        <div className="flex items-center gap-2">
          <UploadCloud className="h-4 w-4 text-brand" />
          <span className="text-sm font-semibold text-ink">
            {fileMode ? "Upload source files" : "Upload source files (optional)"}
          </span>
        </div>
        <p className="text-[12px] text-ink-muted">
          {fileMode
            ? "Upload your source extracts (CSV / XLSX). Each file is auto-detected to its Oracle Fusion FBDI object; on finish, the workbench creates one conversion per file — ready to map and export as an FBDI template."
            : "Working from file exports instead of (or in addition to) a live connection? Upload your source extracts (CSV / XLSX). Each file is auto-detected to its source and target FBDI object; on finish, the workbench creates one conversion per file — ready to map and export as an FBDI template."}
        </p>
        <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-line bg-canvas px-4 py-4 hover:border-brand">
          <UploadCloud className="h-5 w-5 text-brand" />
          <span className="text-sm text-ink">Choose one or more CSV / XLSX files</span>
          <input type="file" multiple accept=".csv,.xlsx,.xls" className="hidden"
            onChange={(e) => { add(e.target.files); e.currentTarget.value = ""; }} />
        </label>
        {items.length > 0 && (
          <div className="space-y-1">
            {items.map((it) => (
              <div key={it.key} className="flex items-center gap-2 rounded border border-line bg-white px-2.5 py-1.5 text-[12px]">
                <Database className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
                <span className="min-w-0 flex-1 truncate text-ink">{it.file.name}</span>
                {it.status === "uploading" && <Pill tone="neutral">analyzing…</Pill>}
                {it.status === "ready" && <Pill tone="success">ready</Pill>}
                {it.status === "error" && <Pill tone="danger">error</Pill>}
                <button onClick={() => setItems((prev) => prev.filter((x) => x.key !== it.key))} className="rounded p-0.5 text-ink-subtle hover:text-danger">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            {items.some((it) => it.status === "error") && (
              <p className="text-[11px] text-danger">
                {items.filter((it) => it.status === "error").map((it) => `${it.file.name}: ${it.error}`).join(" · ")}
              </p>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
};

const Step4Scope: React.FC<{
  modules: FusionModule[];
  sourceCode: string;
  isMock: boolean;
  /** Live EBS connection details — only present when isMock=false and sourceCode=oracle_ebs */
  liveConn?: { host: string; port: number; service_name: string; username: string; password: string };
  selected: string[];
  onChange: (codes: string[]) => void;
}> = ({ modules, sourceCode, isMock, liveConn, selected, onChange }) => {
  const toggle = (code: string) => {
    if (selected.includes(code)) {
      onChange(selected.filter((c) => c !== code));
    } else {
      onChange([...selected, code]);
    }
  };

  // Live EBS row counts fetched on mount when liveConn is available
  const [liveCounts, setLiveCounts] = useState<Record<string, number | null>>({});
  const [scanState, setScanState] = useState<"idle" | "scanning" | "done" | "error">("idle");
  const hasFetched = useRef(false);

  // Compute the de-duplicated set of conversions that will be created.
  const objectsToCreate = new Map<string, {
    label: string;
    planned: number;
    sourceHint: string;
    mockRowCount?: number;
    ebsTable?: string;   // parsed table name (uppercase) from sourceHint
  }>();
  modules
    .filter((m) => selected.includes(m.code))
    .forEach((m) => {
      m.objects.forEach((o) => {
        if (!objectsToCreate.has(o.target_object)) {
          const hint = o.source_extracts[sourceCode] || "—";
          // Parse table name: strip "Extract from", take first token, strip conditions
          const tableMatch = hint.replace(/^Extract from\s+/i, "").split(/[\s(/]/)[0].toUpperCase();
          objectsToCreate.set(o.target_object, {
            label: o.label,
            planned: o.planned_load_order,
            sourceHint: hint,
            mockRowCount: o.mock_row_counts?.[sourceCode],
            ebsTable: tableMatch !== "—" ? tableMatch : undefined,
          });
        }
      });
    });

  // Trigger one live scan when entering scope step with a live EBS connection
  const fetchLiveCounts = useCallback(async () => {
    if (!liveConn || isMock || hasFetched.current) return;
    hasFetched.current = true;
    setScanState("scanning");
    const allHints = [...objectsToCreate.values()]
      .map((o) => o.sourceHint)
      .filter((h) => h !== "—");
    if (allHints.length === 0) { setScanState("done"); return; }
    try {
      const result = await DiscoveryApi.quickTableCounts({
        ...liveConn,
        source_extracts: allHints,
      });
      setLiveCounts(result.counts);
      setScanState("done");
    } catch {
      setScanState("error");
    }
  }, [liveConn, isMock, objectsToCreate.size]);

  useEffect(() => {
    fetchLiveCounts();
  }, [fetchLiveCounts]);

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
            <div className="flex items-center justify-between">
              <div className="text-[10.5px] font-semibold uppercase tracking-wider text-brand-dark">
                {objectsToCreate.size} conversion{objectsToCreate.size === 1 ? "" : "s"} will be auto-created
              </div>
              {!isMock && scanState === "scanning" && (
                <span className="inline-flex items-center gap-1 text-[10px] text-brand animate-pulse">
                  <Database className="h-3 w-3" />
                  Scanning live EBS…
                </span>
              )}
              {!isMock && scanState === "done" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                  <Database className="h-3 w-3" /> live EBS counts
                </span>
              )}
              {!isMock && scanState === "error" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-medium text-rose-700">
                  <Database className="h-3 w-3" /> EBS scan failed
                </span>
              )}
              {!isMock && scanState === "idle" && (
                <span className="inline-flex items-center gap-1 text-[10px] text-ink-muted">
                  <Database className="h-3 w-3" />
                  Row counts after first Discovery scan
                </span>
              )}
              {isMock && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                  <Workflow className="h-3 w-3" /> mock estimates
                </span>
              )}
            </div>
            <table className="mt-2 w-full text-[11.5px]">
              <thead className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
                <tr>
                  <th className="pb-1 pr-2">Object</th>
                  <th className="pb-1 pr-2">Load order</th>
                  <th className="pb-1 pr-2">Source extract</th>
                  <th className="pb-1">Rows</th>
                </tr>
              </thead>
              <tbody>
                {[...objectsToCreate.entries()]
                  .sort((a, b) => a[1].planned - b[1].planned)
                  .map(([target, info]) => (
                    <tr key={target} className="border-t border-line/60">
                      <td className="py-1 pr-2 font-medium text-ink">{info.label}</td>
                      <td className="py-1 pr-2 font-mono text-ink-muted">{info.planned}</td>
                      <td className="py-1 pr-2 font-mono text-[10.5px] text-ink-muted">
                        {info.sourceHint !== "—" ? (
                          <>
                            {info.sourceHint.replace(/^Extract from\s+/, "")}
                          </>
                        ) : "—"}
                      </td>
                      <td className="py-1 whitespace-nowrap">
                        {isMock ? (
                          info.mockRowCount !== undefined ? (
                            <span className="font-mono text-[10.5px] text-amber-700">
                              ~{info.mockRowCount.toLocaleString()}
                            </span>
                          ) : (
                            <span className="text-[10px] text-ink-muted">—</span>
                          )
                        ) : scanState === "scanning" ? (
                          <span className="text-[10px] italic text-brand animate-pulse">scanning…</span>
                        ) : scanState === "done" && info.ebsTable && liveCounts[info.ebsTable] != null ? (
                          <span className="font-mono text-[10.5px] font-semibold text-emerald-700">
                            {(liveCounts[info.ebsTable] as number).toLocaleString()}
                          </span>
                        ) : scanState === "done" && info.ebsTable && liveCounts[info.ebsTable] === null ? (
                          <span className="text-[10px] text-rose-500" title="Table not accessible">—</span>
                        ) : (
                          <span className="text-[10px] italic text-ink-muted">pending scan</span>
                        )}
                      </td>
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
  isFileMode?: boolean;
  fileSummary?: { key: string; fileName: string; objectLabel: string; convLabels: string[] }[];
}> = ({ details, sourceSystem, conn, selectedModules, allModules, isFileMode, fileSummary = [] }) => {
  const scopedModules = allModules.filter((m) => selectedModules.includes(m.code));
  const uniqueObjects = new Set<string>();
  scopedModules.forEach((m) => m.objects.forEach((o) => uniqueObjects.add(o.target_object)));
  const totalFileConvs = fileSummary.reduce((s, f) => s + f.convLabels.length, 0);
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
            {isFileMode ? (
              <>
                <ReviewRow k="System" v="File upload" />
                <ReviewRow k="Format" v="CSV / XLSX extract" />
                <ReviewRow k="Target" v="Oracle Fusion FBDI" />
              </>
            ) : (
              <>
                <ReviewRow k="System" v={sourceSystem?.display_name || "—"} />
                <ReviewRow k="Family" v={sourceSystem?.family?.toUpperCase() || "—"} />
                <ReviewRow k="Scanner"
                  v={sourceSystem?.has_scanner_v1 ? "Ready (mock)" : "Mock only for v1"} />
              </>
            )}
          </ReviewBlock>
          <ReviewBlock title={isFileMode ? "Source file" : "Connection"}>
            {isFileMode ? (
              fileSummary.length === 0 ? (
                <div className="text-[11px] text-ink-muted">No file uploaded.</div>
              ) : (
                fileSummary.map((f, i) => (
                  <ReviewRow
                    key={f.key}
                    k={fileSummary.length > 1 ? `File ${i + 1}` : "File"}
                    v={f.fileName}
                  />
                ))
              )
            ) : (
              <>
                <ReviewRow k="Display name" v={conn.display_name} />
                <ReviewRow k="Endpoint"     v={conn.endpoint || "—"} />
                <ReviewRow k="Auth type"    v={conn.auth_type} />
                <ReviewRow k="Mode"
                  v={conn.mock_mode ? "Mock (fixtures)" : "Live (sealed credentials)"} />
              </>
            )}
          </ReviewBlock>
          <ReviewBlock title="Implementation scope">
            {isFileMode ? (
              totalFileConvs === 0 ? (
                <div className="text-[11px] text-ink-muted">
                  No conversions — upload a source file on the Connection step.
                </div>
              ) : (
                <>
                  <div className="mb-1.5 text-[11px] font-semibold text-ink">
                    {totalFileConvs} FBDI conversion{totalFileConvs === 1 ? "" : "s"} will be created
                  </div>
                  {fileSummary.map((f) => (
                    <div key={f.key} className="mb-1.5 last:mb-0">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                        {f.objectLabel}
                      </div>
                      <div className="mt-0.5 space-y-0.5">
                        {f.convLabels.map((l, i) => (
                          <div key={l} className="flex items-center gap-1.5 text-[11px] text-ink">
                            <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-brand/10 font-mono text-[8.5px] text-brand-dark">
                              {i + 1}
                            </span>
                            <span className="truncate">{l}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </>
              )
            ) : scopedModules.length === 0 ? (
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
          {isFileMode ? (
            <>
              Creating the engagement saves the project, your uploaded source file,
              {totalFileConvs > 0 && (
                <> {totalFileConvs} FBDI conversion{totalFileConvs === 1 ? "" : "s"},</>
              )}
              {" "}and an audit-log entry — all atomically.
            </>
          ) : (
            <>
              Creating the engagement saves the project, the source connection,
              {uniqueObjects.size > 0 && (
                <> {uniqueObjects.size} planned conversion{uniqueObjects.size === 1 ? "" : "s"},</>
              )}
              {" "}and an audit-log entry — all atomically.
            </>
          )}
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
