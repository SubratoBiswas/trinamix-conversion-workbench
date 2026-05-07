import React, { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, Database, FileSpreadsheet, Sparkles, ShieldCheck,
  ListChecks, Play, Download, FileOutput, ArrowRight, Workflow as WfIcon,
  Eye, Cloud, GitBranch, CheckCircle2, Clock, XCircle, Loader2,
} from "lucide-react";
import {
  ConversionsApi, CutoverApi, DatasetsApi, FbdiApi, LoadApi, MappingApi,
  OutputApi, ProjectsApi, QualityApi,
} from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { PromoteToEnvironmentModal } from "@/components/cutover/PromoteToEnvironmentModal";
import { cn, formatDate, statusTone } from "@/lib/utils";
import type {
  Conversion, ConvertedOutput, Dataset, Environment, EnvironmentRun,
  FBDITemplate, LoadRun, MappingSuggestion, Project, ValidationIssue,
} from "@/types";

/**
 * Operations page for a single Conversion object. The user runs AI mapping,
 * cleansing, validation, output generation, and load simulation from here.
 */
export const ConversionDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const cid = Number(id);
  const nav = useNavigate();

  const [conv, setConv] = useState<Conversion | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [template, setTemplate] = useState<FBDITemplate | null>(null);

  const [mappings, setMappings] = useState<MappingSuggestion[]>([]);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [outputs, setOutputs] = useState<ConvertedOutput[]>([]);
  const [loadRuns, setLoadRuns] = useState<LoadRun[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [envRuns, setEnvRuns] = useState<EnvironmentRun[]>([]);

  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };

  const loadAll = async () => {
    const c = await ConversionsApi.get(cid);
    setConv(c);
    if (c.project_id) {
      ProjectsApi.get(c.project_id).then(setProject);
      CutoverApi.environments(c.project_id).then(setEnvironments);
    }
    if (c.dataset_id) DatasetsApi.get(c.dataset_id).then((d) => setDataset(d));
    if (c.template_id) FbdiApi.get(c.template_id).then((t) => setTemplate(t));
    CutoverApi.runsForConversion(cid).then(setEnvRuns).catch(() => setEnvRuns([]));
    if (c.dataset_id && c.template_id) {
      MappingApi.list(cid).then(setMappings).catch(() => setMappings([]));
      QualityApi.cleansing(cid).then((cl) =>
        QualityApi.validation(cid).then((vl) => setIssues([...cl, ...vl]))
      ).catch(() => {});
      LoadApi.runs(cid).then(setLoadRuns).catch(() => setLoadRuns([]));
    }
  };
  useEffect(() => { loadAll(); }, [cid]);

  if (!conv) return <PageLoader />;

  const isPlanning = conv.status === "planning";
  const isFullyBound = !!conv.dataset_id && !!conv.template_id;

  const runOp = async (op: string, fn: () => Promise<any>, successMsg: string) => {
    setBusy(op);
    try {
      await fn();
      flash(successMsg);
      loadAll();
    } catch (e: any) {
      flash(`Error: ${e?.response?.data?.detail || e?.message || "operation failed"}`);
    } finally { setBusy(null); }
  };

  return (
    <>
      <PageTitle
        title={conv.name}
        subtitle={
          <span className="flex items-center gap-2 text-[12.5px]">
            {project && (
              <>
                <Link to={`/projects/${project.id}`} className="text-brand-dark hover:underline">
                  {project.name}
                </Link>
                <ArrowRight className="h-3 w-3 text-ink-subtle" />
              </>
            )}
            <span>{conv.target_object || "—"}</span>
            <Pill tone={statusTone(conv.status)}>{conv.status.replace("_", " ")}</Pill>
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Link to="/conversions" className="btn-ghost">
              <ArrowLeft className="h-4 w-4" /> All conversions
            </Link>
            {project && isFullyBound && (
              <Button onClick={() => setPromoteOpen(true)}>
                <ArrowRight className="h-4 w-4" /> Promote to environment
              </Button>
            )}
          </div>
        }
      />

      {/* Environment progression strip — shows DEV → QA → UAT → PROD status */}
      {environments.length > 0 && (
        <EnvironmentStrip
          conversion={conv}
          environments={environments}
          runs={envRuns}
          onPromote={() => setPromoteOpen(true)}
        />
      )}

      {/* Bindings strip — shows source + target + lets user fix gaps */}
      <Card className="mb-4">
        <CardBody>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="flex items-center gap-3 rounded-md border border-line bg-canvas px-3 py-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-50 text-emerald-600">
                <Database className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Source dataset</div>
                {dataset ? (
                  <Link to={`/datasets/${dataset.id}/prepare`} className="block truncate text-sm font-semibold text-ink hover:text-brand-dark">
                    {dataset.name}
                    <span className="ml-1.5 font-mono text-[10.5px] text-ink-muted">
                      {dataset.row_count.toLocaleString()} × {dataset.column_count}
                    </span>
                  </Link>
                ) : (
                  <div className="text-sm italic text-ink-subtle">Awaiting source file</div>
                )}
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-md border border-line bg-canvas px-3 py-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
                <FileSpreadsheet className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">Target FBDI</div>
                {template ? (
                  <Link to={`/fbdi/${template.id}`} className="block truncate text-sm font-semibold text-ink hover:text-brand-dark">
                    {template.name}
                    {template.business_object && (
                      <span className="ml-1.5 font-mono text-[10.5px] text-ink-muted">{template.business_object}</span>
                    )}
                  </Link>
                ) : (
                  <div className="text-sm italic text-ink-subtle">No FBDI selected</div>
                )}
              </div>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* Action toolbar */}
      <Card className="mb-4">
        <CardHeader title="Conversion Pipeline" subtitle="Run each stage in order, or jump to the dedicated workspace" />
        <CardBody>
          {!isFullyBound ? (
            <EmptyState
              icon={<Sparkles className="h-5 w-5" />}
              title="Conversion is not fully bound"
              description="This conversion is in planning status. Add a source dataset and a target FBDI template to enable AI mapping, validation, output generation, and load simulation."
            />
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                loading={busy === "ai_map"}
                onClick={() => runOp("ai_map",
                  async () => { await MappingApi.suggest(cid); nav(`/mappings?conversion=${cid}`); },
                  "AI mapping run — opening Mapping Review"
                )}
              >
                <Sparkles className="h-4 w-4" /> AI Auto Map
              </Button>
              <Button
                variant="secondary"
                loading={busy === "cleansing"}
                onClick={() => runOp("cleansing",
                  () => QualityApi.runCleansing(cid),
                  "Cleansing analysis complete"
                )}
              >
                <ShieldCheck className="h-4 w-4" /> Run Cleansing
              </Button>
              <Button
                variant="secondary"
                loading={busy === "validate"}
                onClick={() => runOp("validate",
                  () => QualityApi.runValidation(cid),
                  "Validation complete"
                )}
              >
                <ListChecks className="h-4 w-4" /> Run Validation
              </Button>
              <Button
                variant="secondary"
                loading={busy === "output"}
                onClick={() => runOp("output",
                  () => OutputApi.generate(cid, "csv"),
                  "Output generated"
                )}
              >
                <FileOutput className="h-4 w-4" /> Generate Output
              </Button>
              <Button
                variant="secondary"
                loading={busy === "load"}
                onClick={() => runOp("load",
                  () => LoadApi.simulate(cid),
                  "Load simulated"
                )}
              >
                <Play className="h-4 w-4" /> Simulate Load
              </Button>
              <a href={OutputApi.downloadUrl(cid)} className="btn-ghost">
                <Download className="h-4 w-4" /> Download Output
              </a>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Status grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Mapping summary */}
        <Card>
          <CardHeader
            title={<><Sparkles className="mr-2 inline h-4 w-4 text-brand" />Mappings</>}
            subtitle={`${mappings.length} suggestion(s)`}
            actions={
              <Link to={`/mappings?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Review <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            <Stat
              items={[
                { label: "Auto-mapped", value: mappings.filter(m => m.source_column).length, tone: "text-info" },
                { label: "Approved",    value: mappings.filter(m => m.status === "approved").length, tone: "text-success" },
                { label: "Required gaps", value: mappings.filter(m => m.target_required && !m.source_column).length, tone: "text-danger" },
              ]}
            />
          </CardBody>
        </Card>

        {/* Quality issues */}
        <Card>
          <CardHeader
            title={<><ShieldCheck className="mr-2 inline h-4 w-4 text-warning" />Quality Issues</>}
            subtitle={`${issues.length} total issue(s)`}
            actions={
              <Link to={`/validation?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Review <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            <Stat
              items={[
                { label: "Cleansing", value: issues.filter(i => i.category === "cleansing").length, tone: "text-info" },
                { label: "Validation", value: issues.filter(i => i.category === "validation").length, tone: "text-warning" },
                { label: "Critical",  value: issues.filter(i => i.severity === "critical" || i.severity === "error").length, tone: "text-danger" },
              ]}
            />
          </CardBody>
        </Card>

        {/* Output */}
        <Card>
          <CardHeader
            title={<><Eye className="mr-2 inline h-4 w-4 text-brand" />Output</>}
            subtitle={outputs.length === 0 ? "Not generated" : `${outputs.length} version(s)`}
            actions={
              <Link to={`/conversions/${cid}/output`} className="btn-ghost h-7 px-2 text-xs">
                Preview <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            {outputs.length === 0 ? (
              <div className="text-xs text-ink-muted">No output generated yet — run "Generate Output" above.</div>
            ) : (
              <div className="space-y-1">
                {outputs.slice(0, 3).map(o => (
                  <div key={o.id} className="flex items-center justify-between rounded-md bg-canvas px-2 py-1.5 text-xs">
                    <span className="truncate font-mono">{o.output_file_name}</span>
                    <span className="text-ink-muted">{o.row_count.toLocaleString()} rows</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Load runs */}
        <Card>
          <CardHeader
            title={<><Cloud className="mr-2 inline h-4 w-4 text-info" />Load Runs</>}
            subtitle={`${loadRuns.length} run(s)`}
            actions={
              <Link to={`/load?conversion=${cid}`} className="btn-ghost h-7 px-2 text-xs">
                Dashboard <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <CardBody>
            {loadRuns.length === 0 ? (
              <div className="text-xs text-ink-muted">No load runs yet.</div>
            ) : (
              <div className="space-y-1">
                {loadRuns.slice(0, 3).map(r => (
                  <div key={r.id} className="flex items-center justify-between rounded-md bg-canvas px-2 py-1.5 text-xs">
                    <span className="font-mono">#{r.id} · {r.run_type}</span>
                    <span className="text-success">{r.passed_count} ✓</span>
                    <span className="text-danger">{r.failed_count} ✕</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}

      {/* Promote-to-environment modal */}
      {project && (
        <PromoteToEnvironmentModal
          open={promoteOpen}
          onClose={() => setPromoteOpen(false)}
          conversion={conv}
          project={project}
          onPromoted={(run) => {
            flash(`Promoted to ${run.environment_name}`);
            loadAll();
          }}
        />
      )}
    </>
  );
};

// ─────── Environment progression strip (DEV → QA → UAT → PROD) ───────

const EnvironmentStrip: React.FC<{
  conversion: Conversion;
  environments: Environment[];
  runs: EnvironmentRun[];
  onPromote: () => void;
}> = ({ conversion, environments, runs, onPromote }) => {
  const sorted = [...environments].sort((a, b) => a.sort_order - b.sort_order);

  // Map env_id → most-recent run for display.
  const runByEnvId = new Map<number, EnvironmentRun>();
  for (const r of runs) {
    const existing = runByEnvId.get(r.environment_id);
    if (!existing || r.id > existing.id) runByEnvId.set(r.environment_id, r);
  }

  // For DEV, derive status from the conversion itself.
  const devStatus =
    conversion.status === "loaded" ? "complete" :
    conversion.status === "failed" ? "failed" :
    ["draft", "mapping_suggested", "awaiting_approval", "validated", "output_generated"]
      .includes(conversion.status) ? "running" :
    "pending";

  return (
    <Card className="mb-4">
      <CardBody className="!p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Environment progression
          </div>
          <button onClick={onPromote} className="text-[11px] font-medium text-brand-dark hover:underline">
            Promote →
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          {sorted.map((env, idx) => {
            const isDev = env.name === "DEV";
            const run = runByEnvId.get(env.id);
            const status = isDev ? devStatus : (run?.status ?? "pending");
            const tone = STATUS_INDICATOR[status] || STATUS_INDICATOR.pending;
            const Icon = tone.icon;

            return (
              <React.Fragment key={env.id}>
                <div
                  className={cn(
                    "flex flex-1 items-center gap-2 rounded-md border px-2.5 py-1.5",
                    tone.cardClass,
                  )}
                >
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", tone.iconClass, tone.spin && "animate-spin")} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[11.5px] font-bold tracking-wider text-ink">{env.name}</div>
                    <div className="text-[10px] text-ink-muted">
                      {isDev && run === undefined ? "build env" : status}
                      {run?.dataset_name && (
                        <span className="ml-1 truncate text-ink-subtle">· {run.dataset_name}</span>
                      )}
                    </div>
                  </div>
                  {env.sox_controlled === 1 && (
                    <ShieldCheck className="h-3 w-3 shrink-0 text-warning" />
                  )}
                </div>
                {idx < sorted.length - 1 && (
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
};

const STATUS_INDICATOR: Record<string, {
  cardClass: string; iconClass: string; icon: React.ElementType; spin?: boolean;
}> = {
  complete: { cardClass: "border-success/40 bg-success-subtle/30", iconClass: "text-success", icon: CheckCircle2 },
  running:  { cardClass: "border-brand/40 bg-brand-subtle/30",     iconClass: "text-brand-dark", icon: Loader2, spin: true },
  pending:  { cardClass: "border-line bg-canvas/50",               iconClass: "text-ink-subtle", icon: Clock },
  failed:   { cardClass: "border-danger/40 bg-danger-subtle/30",   iconClass: "text-danger", icon: XCircle },
  blocked:  { cardClass: "border-warning/40 bg-warning-subtle/30", iconClass: "text-warning", icon: GitBranch },
};

const Stat: React.FC<{ items: { label: string; value: number; tone: string }[] }> = ({ items }) => (
  <div className="grid grid-cols-3 gap-2">
    {items.map((it) => (
      <div key={it.label} className="rounded-md bg-canvas px-2 py-2 text-center">
        <div className={cn("text-2xl font-semibold tabular-nums", it.tone)}>{it.value}</div>
        <div className="text-[10.5px] uppercase tracking-wider text-ink-muted">{it.label}</div>
      </div>
    ))}
  </div>
);
