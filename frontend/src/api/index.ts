import { api } from "./client";
import type {
  AuditEvent,
  ConnectionTestResult,
  Conversion,
  ConversionProject,            // alias kept for legacy callers
  ConvertedOutput,
  CutoverDashboard,
  DashboardKpis,
  Dataset,
  DatasetDetail,
  DatasetPreview,
  COAComposeResult,
  COACrosswalk,
  COASegment,
  COAStructure,
  Dependency,
  DiscoveredObject,
  DiscoveryLatest,
  DiscoveryRun,
  DressRehearsal,
  Environment,
  EnvironmentRun,
  ExecSummary,
  FusionModule,
  Issue,
  ReconciliationCheck,
  Risk,
  RunbookTask,
  SafeguardsResponse,
  SignOff,
  ReadinessScore,
  FBDIField,
  FBDITemplate,
  FBDITemplateDetail,
  KnowledgeBankStat,
  LearnedMapping,
  LearningStats,
  LoadError,
  LoadRun,
  LoadSummary,
  MappingSuggestion,
  OutputPreview,
  Project,
  SourceConnection,
  SourceSystem,
  TransformationRule,
  User,
  ValidationIssue,
  Workflow,
} from "@/types";

export const AuthApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; user: User }>("/auth/login", { email, password }).then(r => r.data),
  me: () => api.get<User>("/auth/me").then(r => r.data),
};

export const DatasetsApi = {
  list: () => api.get<Dataset[]>("/datasets").then(r => r.data),
  get: (id: number) => api.get<DatasetDetail>(`/datasets/${id}`).then(r => r.data),
  preview: (id: number, limit = 50) =>
    api.get<DatasetPreview>(`/datasets/${id}/preview`, { params: { limit } }).then(r => r.data),
  upload: (file: File, name?: string, description?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    if (description) fd.append("description", description);
    return api.post<DatasetDetail>("/datasets/upload", fd).then(r => r.data);
  },
  /** P3 — toggle the PII / sensitivity flag on a dataset column. */
  setColumnPII: (
    columnId: number,
    body: { contains_pii: boolean; pii_category?: string | null },
  ) =>
    api.patch<DatasetDetail>(`/datasets/columns/${columnId}/pii`, body).then(r => r.data),
};

export const FbdiApi = {
  list: () => api.get<FBDITemplate[]>("/fbdi/templates").then(r => r.data),
  get: (id: number) => api.get<FBDITemplateDetail>(`/fbdi/templates/${id}`).then(r => r.data),
  fields: (id: number) => api.get<FBDIField[]>(`/fbdi/templates/${id}/fields`).then(r => r.data),
  updateField: (id: number, body: Partial<FBDIField>) =>
    api.put<FBDIField>(`/fbdi/fields/${id}`, body).then(r => r.data),
  upload: (file: File, opts: { name?: string; module?: string; business_object?: string } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    if (opts.name) fd.append("name", opts.name);
    if (opts.module) fd.append("module", opts.module);
    if (opts.business_object) fd.append("business_object", opts.business_object);
    return api.post<FBDITemplateDetail>("/fbdi/upload", fd).then(r => r.data);
  },
};

// ─── Engagement-level (Projects) ───
export const ProjectsApi = {
  list: () => api.get<Project[]>("/projects").then(r => r.data),
  get: (id: number) => api.get<Project>(`/projects/${id}`).then(r => r.data),
  create: (body: Partial<Project> & {
    initial_connection?: {
      source_system: string;
      display_name: string;
      endpoint?: string;
      auth_type?: string;
      connection_metadata?: Record<string, any>;
      credentials?: Record<string, any>;
      mock_mode?: boolean;
    };
  }) => api.post<Project>("/projects", body).then(r => r.data),
  update: (id: number, body: Partial<Project>) =>
    api.patch<Project>(`/projects/${id}`, body).then(r => r.data),
  remove: (id: number) => api.delete(`/projects/${id}`).then(r => r.data),
  conversions: (id: number) =>
    api.get<Conversion[]>(`/projects/${id}/conversions`).then(r => r.data),
  // Per-project source connections — embedded on Project Overview.
  connections: (id: number) =>
    api.get<SourceConnection[]>(`/projects/${id}/source-connections`).then(r => r.data),
};

// Server-driven enum for the source-system picker. Cached at module level
// because it's read-only and small; refresh by reloading the page.
export const SourceSystemsApi = {
  list: () =>
    api.get<SourceSystem[]>("/source-systems").then(r => r.data),
};

// Fusion module catalog (Setup Wizard "Implementation Scope" step).
export const FusionModulesApi = {
  list: () =>
    api.get<FusionModule[]>("/fusion-modules").then(r => r.data),
};

export const SourceConnectionsApi = {
  create: (body: {
    project_id: number;
    source_system: string;
    display_name: string;
    endpoint?: string;
    auth_type?: string;
    connection_metadata?: Record<string, any>;
    credentials?: Record<string, any>;
    mock_mode?: boolean;
  }) => api.post<SourceConnection>("/source-connections", body).then(r => r.data),
  get: (id: number) =>
    api.get<SourceConnection>(`/source-connections/${id}`).then(r => r.data),
  update: (id: number, body: Partial<SourceConnection> & { credentials?: Record<string, any> }) =>
    api.patch<SourceConnection>(`/source-connections/${id}`, body).then(r => r.data),
  test: (id: number) =>
    api.post<ConnectionTestResult>(`/source-connections/${id}/test`).then(r => r.data),
  remove: (id: number) =>
    api.delete(`/source-connections/${id}`).then(r => r.data),
};

export const AuditEventsApi = {
  list: (params?: {
    project_id?: number;
    actor?: string;
    action_prefix?: string;
    target_type?: string;
    limit?: number;
  }) => api.get<AuditEvent[]>("/audit-events", { params }).then(r => r.data),
};

// Discovery — inventory scans against the project's source connection.
// Embedded in Project Overview; no top-level route.
export const DiscoveryApi = {
  run: (projectId: number, connectionId?: number) =>
    api
      .post<DiscoveryRun>(
        `/projects/${projectId}/discovery/run`,
        null,
        connectionId ? { params: { connection_id: connectionId } } : undefined
      )
      .then((r) => r.data),
  latest: (projectId: number) =>
    api
      .get<DiscoveryLatest>(`/projects/${projectId}/discovery/latest`)
      .then((r) => r.data),
  objects: (
    runId: number,
    params?: {
      pillar?: string;
      category?: string;
      risk_level?: string;
      limit?: number;
    }
  ) =>
    api
      .get<DiscoveredObject[]>(`/discovery-runs/${runId}/objects`, { params })
      .then((r) => r.data),
  // Slice 5 — re-probe a single integration in place; returns the
  // refreshed row so the UI can swap the status pill without a full
  // /latest fetch.
  reprobe: (objectId: number) =>
    api
      .post<DiscoveredObject>(`/discovered-objects/${objectId}/reprobe`)
      .then((r) => r.data),
};

// ── Slice 6 — Cutover & Exec layer (single namespace) ──────────────

export const Slice6Api = {
  safeguards: (projectId: number) =>
    api.get<SafeguardsResponse>(`/projects/${projectId}/safeguards`).then(r => r.data),
  readiness: (projectId: number) =>
    api.get<ReadinessScore>(`/projects/${projectId}/readiness`).then(r => r.data),
  execSummary: (projectId: number) =>
    api.get<ExecSummary>(`/projects/${projectId}/exec-summary`).then(r => r.data),

  reconciliation: (projectId: number) =>
    api.get<ReconciliationCheck[]>(`/projects/${projectId}/reconciliation`).then(r => r.data),
  seedReconciliation: (projectId: number) =>
    api.post<ReconciliationCheck[]>(`/projects/${projectId}/reconciliation/seed`).then(r => r.data),

  runbook: (projectId: number) =>
    api.get<RunbookTask[]>(`/projects/${projectId}/runbook`).then(r => r.data),
  seedRunbook: (projectId: number, force = false) =>
    api.post<RunbookTask[]>(`/projects/${projectId}/runbook/seed`, null, { params: { force } }).then(r => r.data),
  updateRunbookTask: (taskId: number, body: Partial<RunbookTask>) =>
    api.patch<RunbookTask>(`/runbook-tasks/${taskId}`, body).then(r => r.data),

  issues: (projectId: number, status?: string) =>
    api.get<Issue[]>(`/projects/${projectId}/issues`, { params: { status } }).then(r => r.data),
  createIssue: (projectId: number, body: Partial<Issue> & { title: string }) =>
    api.post<Issue>(`/projects/${projectId}/issues`, body).then(r => r.data),
  updateIssue: (issueId: number, body: Partial<Issue>) =>
    api.patch<Issue>(`/issues/${issueId}`, body).then(r => r.data),

  risks: (projectId: number) =>
    api.get<Risk[]>(`/projects/${projectId}/risks`).then(r => r.data),
  createRisk: (projectId: number, body: Partial<Risk> & { title: string }) =>
    api.post<Risk>(`/projects/${projectId}/risks`, body).then(r => r.data),
  updateRisk: (riskId: number, body: Partial<Risk>) =>
    api.patch<Risk>(`/risks/${riskId}`, body).then(r => r.data),

  dressRehearsals: (projectId: number) =>
    api.get<DressRehearsal[]>(`/projects/${projectId}/dress-rehearsals`).then(r => r.data),
  createDressRehearsal: (projectId: number, body: Partial<DressRehearsal>) =>
    api.post<DressRehearsal>(`/projects/${projectId}/dress-rehearsals`, body).then(r => r.data),

  signOffs: (projectId: number) =>
    api.get<SignOff[]>(`/projects/${projectId}/sign-offs`).then(r => r.data),
  createSignOff: (projectId: number, body: Partial<SignOff> & {
    kind: string; subject: string; signer_email: string; signer_role: string;
  }) =>
    api.post<SignOff>(`/projects/${projectId}/sign-offs`, body).then(r => r.data),

  // P6 — COA coverage gate. Drives the banner in the Sign-off Capture
  // modal: "Cutover-Go is BLOCKED — GL Coding Combinations COA coverage
  // is 87.4% (threshold 99%)".
  coaReadiness: (projectId: number) =>
    api.get<{
      threshold_pct: number; is_ready: boolean;
      worst_coverage_pct: number | null;
      blocker_reason: string | null;
      conversions: {
        conversion_id: number; conversion_name: string;
        has_structure: boolean; has_dataset: boolean;
        coverage_pct: number | null;
        total_rows: number; invalid_rows: number;
        gaps_by_segment: Record<string, number>;
        blocker_reason: string | null;
      }[];
    }>(`/projects/${projectId}/coa-readiness`).then(r => r.data),

  promoteEnvironment: (projectId: number, target_environment: string) =>
    api.post<{ current_environment: string; promoted_from: string }>(
      `/projects/${projectId}/promote-environment`, { target_environment }
    ).then(r => r.data),

  // P2 — Data Quality Score per conversion + project recompute.
  conversionQualityScore: (conversionId: number) =>
    api.get<{
      conversion_id: number; total: number;
      lenses: { code: string; value_pct: number; weight: number; details: any }[];
    }>(`/conversions/${conversionId}/quality-score`).then(r => r.data),
  recomputeProjectQualityScores: (projectId: number) =>
    api.post<{ project_id: number; scores: Record<number, number>; average: number }>(
      `/projects/${projectId}/quality-score/recompute`
    ).then(r => r.data),

  projectLoadRuns: (projectId: number, environment?: string) =>
    api.get<LoadRun[]>(`/projects/${projectId}/load-runs`,
      { params: environment ? { environment } : undefined }
    ).then(r => r.data),
};

export const CopilotApi = {
  ask: (body: {
    project_id: number;
    messages: { role: "user" | "assistant"; content: string }[];
  }) => api.post<{ answer: string; citations: string[] }>("/copilot/ask", body).then(r => r.data),
};

// ── Slice 7 — COA Engine ────────────────────────────────────────────

export const COAApi = {
  structure: (conversionId: number) =>
    api.get<COAStructure | null>(`/conversions/${conversionId}/coa`).then(r => r.data),
  seed: (conversionId: number) =>
    api.post<COAStructure>(`/conversions/${conversionId}/coa/seed`).then(r => r.data),
  updateStructure: (structureId: number, body: Partial<COAStructure>) =>
    api.patch<COAStructure>(`/coa-structures/${structureId}`, body).then(r => r.data),

  addSegment: (structureId: number, body: Partial<COASegment> & { name: string; length: number }) =>
    api.post<COASegment>(`/coa-structures/${structureId}/segments`, body).then(r => r.data),
  updateSegment: (segmentId: number, body: Partial<COASegment>) =>
    api.patch<COASegment>(`/coa-segments/${segmentId}`, body).then(r => r.data),
  removeSegment: (segmentId: number) =>
    api.delete(`/coa-segments/${segmentId}`).then(r => r.data),

  crosswalks: (segmentId: number) =>
    api.get<COACrosswalk[]>(`/coa-segments/${segmentId}/crosswalks`).then(r => r.data),
  upsertCrosswalk: (segmentId: number, body: { legacy_value: string; fusion_value: string; description?: string; notes?: string }) =>
    api.post<COACrosswalk>(`/coa-segments/${segmentId}/crosswalks`, body).then(r => r.data),
  bulkUpsertCrosswalk: (segmentId: number, rows: { legacy_value: string; fusion_value: string }[]) =>
    api.post<COACrosswalk[]>(`/coa-segments/${segmentId}/crosswalks/bulk`, { rows }).then(r => r.data),
  removeCrosswalk: (crosswalkId: number) =>
    api.delete(`/coa-crosswalks/${crosswalkId}`).then(r => r.data),

  compose: (conversionId: number, sampleSize = 25) =>
    api.post<COAComposeResult>(`/conversions/${conversionId}/coa/compose`,
      null, { params: { sample_size: sampleSize } }
    ).then(r => r.data),
};

// ─── Conversion-object-level (Conversions) ───
// Every operation that used to live under /api/projects/{id}/* now lives under
// /api/conversions/{id}/*.
export const ConversionsApi = {
  list: (params?: { project_id?: number; status?: string }) =>
    api.get<Conversion[]>("/conversions", { params }).then(r => r.data),
  get: (id: number) => api.get<Conversion>(`/conversions/${id}`).then(r => r.data),
  create: (body: Partial<Conversion>) =>
    api.post<Conversion>("/conversions", body).then(r => r.data),
  update: (id: number, body: Partial<Conversion>) =>
    api.patch<Conversion>(`/conversions/${id}`, body).then(r => r.data),
  remove: (id: number) => api.delete(`/conversions/${id}`).then(r => r.data),
};

export const MappingApi = {
  suggest: (conversionId: number) =>
    api.post<MappingSuggestion[]>(`/conversions/${conversionId}/suggest-mapping`).then(r => r.data),
  list: (conversionId: number) =>
    api.get<MappingSuggestion[]>(`/conversions/${conversionId}/mappings`).then(r => r.data),
  update: (mappingId: number, body: Partial<MappingSuggestion>) =>
    api.put<MappingSuggestion>(`/mappings/${mappingId}`, body).then(r => r.data),
  approve: (mappingId: number) =>
    api.put<MappingSuggestion>(`/mappings/${mappingId}/approve`).then(r => r.data),
  rules: (conversionId: number) =>
    api.get<TransformationRule[]>(`/conversions/${conversionId}/rules`).then(r => r.data),
  addRule: (conversionId: number, body: {
    target_field_id?: number; source_column?: string; rule_type: string;
    rule_config: any; description?: string;
  }) =>
    api.post<TransformationRule>(`/conversions/${conversionId}/rules`, body).then(r => r.data),
  deleteRule: (ruleId: number) => api.delete(`/rules/${ruleId}`).then(r => r.data),
  previewRules: (
    conversionId: number,
    body: {
      rules: { rule_type: string; config: any }[];
      source_column?: string;
      sample_size?: number;
    }
  ) =>
    api
      .post<{
        samples: { source: any; output: any; error?: string | null }[];
      }>(`/conversions/${conversionId}/rules/preview`, body)
      .then((r) => r.data),
  // Slice 3 + bug-fix — natural-language rule translator. Local
  // pattern matcher tries first (no API call). Claude is the fallback.
  // 503 only fires when local can't match AND no Anthropic key is
  // configured — much rarer than before. Response carries a "source"
  // field ("local" | "ai") so the modal can show provenance.
  translateRule: (
    conversionId: number,
    body: {
      description: string;
      target_field_id?: number;
      source_column?: string;
      sample_size?: number;
    }
  ) =>
    api
      .post<{
        rule_type: string;
        config: any;
        explanation: string;
        ambiguities: { phrase: string; interpreted_as: string; alternatives: string[] }[];
        preview_samples: { source: any; output: any; error?: string | null }[];
        source: "local" | "ai";
      }>(`/conversions/${conversionId}/rules/translate`, body)
      .then((r) => r.data),
};

export const QualityApi = {
  runCleansing: (conversionId: number) =>
    api.post<ValidationIssue[]>(`/conversions/${conversionId}/profile-cleansing`).then(r => r.data),
  cleansing: (conversionId: number) =>
    api.get<ValidationIssue[]>(`/conversions/${conversionId}/cleansing-issues`).then(r => r.data),
  runValidation: (conversionId: number) =>
    api.post<ValidationIssue[]>(`/conversions/${conversionId}/validate`).then(r => r.data),
  validation: (conversionId: number) =>
    api.get<ValidationIssue[]>(`/conversions/${conversionId}/validation-issues`).then(r => r.data),
};

export const OutputApi = {
  generate: (conversionId: number, fmt: "csv" | "xlsx" = "csv") =>
    api.post<ConvertedOutput>(`/conversions/${conversionId}/generate-output`, null, { params: { fmt } }).then(r => r.data),
  preview: (conversionId: number, limit = 50) =>
    api.get<OutputPreview>(`/conversions/${conversionId}/output-preview`, { params: { limit } }).then(r => r.data),
  downloadUrl: (conversionId: number) => `/api/conversions/${conversionId}/download-output`,
};

export const LoadApi = {
  simulate: (conversionId: number) =>
    api.post<LoadRun>(`/conversions/${conversionId}/simulate-load`).then(r => r.data),
  runs: (conversionId: number) =>
    api.get<LoadRun[]>(`/conversions/${conversionId}/load-runs`).then(r => r.data),
  errors: (runId: number) => api.get<LoadError[]>(`/load-runs/${runId}/errors`).then(r => r.data),
  /** Errors from this conversion's most recent load run — convenience for the
   * Error Traceback drawer (no need to fetch run id separately). */
  latestErrors: (conversionId: number) =>
    api.get<LoadError[]>(`/conversions/${conversionId}/load-errors`).then(r => r.data),
  summary: (conversionId: number) =>
    api.get<LoadSummary>(`/conversions/${conversionId}/load-summary`).then(r => r.data),
};

export const WorkflowApi = {
  list: () => api.get<Workflow[]>("/workflows").then(r => r.data),
  get: (id: number) => api.get<Workflow>(`/workflows/${id}`).then(r => r.data),
  create: (body: any) => api.post<Workflow>("/workflows", body).then(r => r.data),
  update: (id: number, body: any) => api.put<Workflow>(`/workflows/${id}`, body).then(r => r.data),
  run: (id: number) => api.post<Workflow>(`/workflows/${id}/run`).then(r => r.data),
};

export const DependencyApi = {
  list: () => api.get<Dependency[]>("/dependencies").then(r => r.data),
  impact: (conversionId: number) =>
    api.get<{ object: string; dependencies: any[]; impacts: any[] }>(`/dependencies/impact/${conversionId}`).then(r => r.data),
};

export const DashboardApi = {
  kpis: () => api.get<DashboardKpis>("/dashboard/kpis").then(r => r.data),
};

// Inherited reference-standards on a downstream conversion. Drives the
// "↶ inherited from Item Master" badge in the Mapping Inspector.
export interface InheritedStandard {
  target_field: string;
  master_object: string;
  rule_type: string;
  rule_config: Record<string, any>;
  captured_from: string;
  originated_in_project_id: number | null;
}

export const InheritedStandardsApi = {
  forConversion: (conversionId: number) =>
    api.get<InheritedStandard[]>(`/conversions/${conversionId}/inherited-standards`).then(r => r.data),
};

export const LearningApi = {
  list: (params?: { kind?: string; category?: string; project_id?: number }) =>
    api.get<LearnedMapping[]>("/learned-mappings", { params }).then(r => r.data),
  stats: (params?: { project_id?: number }) =>
    api.get<LearningStats>("/learned-mappings/stats", { params }).then(r => r.data),
  capture: (body: Partial<LearnedMapping>) =>
    api.post<LearnedMapping>("/learned-mappings", body).then(r => r.data),
  delete: (id: number) => api.delete(`/learned-mappings/${id}`).then(r => r.data),
  // Per-source-system rollup for the Learning Center's Knowledge Bank
  // section. Drives the "{Source} · N mappings across M projects" card.
  knowledgeBankStats: () =>
    api.get<KnowledgeBankStat[]>("/learned-mappings/knowledge-bank/stats").then(r => r.data),
};

export const CutoverApi = {
  /** List environments configured for a project. */
  environments: (projectId: number) =>
    api.get<Environment[]>(`/projects/${projectId}/environments`).then(r => r.data),

  /** Idempotently seed the standard DEV/QA/UAT/PROD ladder. */
  seedDefaults: (projectId: number) =>
    api.post<Environment[]>(`/projects/${projectId}/environments/seed`).then(r => r.data),

  /** All environment runs for a conversion (DEV → QA → UAT → PROD progression). */
  runsForConversion: (conversionId: number) =>
    api.get<EnvironmentRun[]>(`/conversions/${conversionId}/environment-runs`).then(r => r.data),

  /** Promote a conversion into a new environment with a fresh dataset upload. */
  promote: (body: {
    environment_id: number;
    conversion_id: number;
    dataset_id?: number | null;
    notes?: string;
  }) =>
    api.post<EnvironmentRun>("/environment-runs", body).then(r => r.data),

  /** Update an environment run (status changes, notes, swap dataset). */
  updateRun: (runId: number, body: Partial<EnvironmentRun>) =>
    api.patch<EnvironmentRun>(`/environment-runs/${runId}`, body).then(r => r.data),

  /** The aggregate cutover dashboard (used by the Migration Monitor page). */
  dashboard: (projectId: number) =>
    api.get<CutoverDashboard>(`/projects/${projectId}/cutover`).then(r => r.data),
};
