import { api } from "./client";
import type {
  AutoPopulateResult,
  Conversion,
  ConversionProject,            // alias kept for legacy callers
  ConvertedOutput,
  CutoverDashboard,
  DashboardKpis,
  Dataset,
  DatasetDetail,
  DatasetPreview,
  Dependency,
  Environment,
  EnvironmentRun,
  FBDIField,
  FBDITemplate,
  FBDITemplateDetail,
  LearnedMapping,
  LearningStats,
  LoadError,
  LoadOrderResult,
  LoadRun,
  LoadSummary,
  MappingSuggestion,
  OutputPreview,
  Project,
  PropagationCandidates,
  PropagationResult,
  TemplateSuggestion,
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
  get: (id: string) => api.get<DatasetDetail>(`/datasets/${id}`).then(r => r.data),
  preview: (id: string, limit = 50) =>
    api.get<DatasetPreview>(`/datasets/${id}/preview`, { params: { limit } }).then(r => r.data),
  suggestTemplate: (id: string) =>
    api.get<{ dataset_id: string; suggestions: TemplateSuggestion[] }>(`/datasets/${id}/suggest-template`).then(r => r.data),
  upload: (file: File, name?: string, description?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    if (description) fd.append("description", description);
    return api.post<DatasetDetail>("/datasets/upload", fd).then(r => r.data);
  },
};

export const FbdiApi = {
  list: () => api.get<FBDITemplate[]>("/fbdi/templates").then(r => r.data),
  get: (id: string) => api.get<FBDITemplateDetail>(`/fbdi/templates/${id}`).then(r => r.data),
  fields: (id: string) => api.get<FBDIField[]>(`/fbdi/templates/${id}/fields`).then(r => r.data),
  updateField: (id: string, body: Partial<FBDIField>) =>
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
  get: (id: string) => api.get<Project>(`/projects/${id}`).then(r => r.data),
  create: (body: Partial<Project>) => api.post<Project>("/projects", body).then(r => r.data),
  update: (id: string, body: Partial<Project>) =>
    api.patch<Project>(`/projects/${id}`, body).then(r => r.data),
  remove: (id: string) => api.delete(`/projects/${id}`).then(r => r.data),
  conversions: (id: string) =>
    api.get<Conversion[]>(`/projects/${id}/conversions`).then(r => r.data),
  autoPopulate: (id: string, modules: string[]) =>
    api.post<AutoPopulateResult>(`/projects/${id}/auto-populate-conversions`, { modules }).then(r => r.data),
  deriveLoadOrder: (id: string) =>
    api.post<LoadOrderResult>(`/projects/${id}/derive-load-order`).then(r => r.data),
};

// ─── Conversion-object-level (Conversions) ───
// Every operation that used to live under /api/projects/{id}/* now lives under
// /api/conversions/{id}/*.
export const ConversionsApi = {
  list: (params?: { project_id?: string; status?: string }) =>
    api.get<Conversion[]>("/conversions", { params }).then(r => r.data),
  get: (id: string) => api.get<Conversion>(`/conversions/${id}`).then(r => r.data),
  create: (body: Partial<Conversion>) =>
    api.post<Conversion>("/conversions", body).then(r => r.data),
  update: (id: string, body: Partial<Conversion>) =>
    api.patch<Conversion>(`/conversions/${id}`, body).then(r => r.data),
  remove: (id: string) => api.delete(`/conversions/${id}`).then(r => r.data),
};

export const MappingApi = {
  propagate: (mappingId: string) =>
    api.post<PropagationResult>(`/mappings/${mappingId}/propagate`).then(r => r.data),
  propagationCandidates: (conversionId: string) =>
    api.get<PropagationCandidates>(`/conversions/${conversionId}/propagation-candidates`).then(r => r.data),
  suggest: (conversionId: string) =>
    api.post<MappingSuggestion[]>(`/conversions/${conversionId}/suggest-mapping`).then(r => r.data),
  list: (conversionId: string) =>
    api.get<MappingSuggestion[]>(`/conversions/${conversionId}/mappings`).then(r => r.data),
  update: (mappingId: string, body: Partial<MappingSuggestion>) =>
    api.put<MappingSuggestion>(`/mappings/${mappingId}`, body).then(r => r.data),
  approve: (mappingId: string) =>
    api.put<MappingSuggestion>(`/mappings/${mappingId}/approve`).then(r => r.data),
  rules: (conversionId: string) =>
    api.get<TransformationRule[]>(`/conversions/${conversionId}/rules`).then(r => r.data),
  addRule: (conversionId: string, body: {
    target_field_id?: string; source_column?: string; rule_type: string;
    rule_config: any; description?: string;
  }) =>
    api.post<TransformationRule>(`/conversions/${conversionId}/rules`, body).then(r => r.data),
  deleteRule: (ruleId: string) => api.delete(`/rules/${ruleId}`).then(r => r.data),
  previewRules: (
    conversionId: string,
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
};

export const QualityApi = {
  runCleansing: (conversionId: string) =>
    api.post<ValidationIssue[]>(`/conversions/${conversionId}/profile-cleansing`).then(r => r.data),
  cleansing: (conversionId: string) =>
    api.get<ValidationIssue[]>(`/conversions/${conversionId}/cleansing-issues`).then(r => r.data),
  runValidation: (conversionId: string) =>
    api.post<ValidationIssue[]>(`/conversions/${conversionId}/validate`).then(r => r.data),
  validation: (conversionId: string) =>
    api.get<ValidationIssue[]>(`/conversions/${conversionId}/validation-issues`).then(r => r.data),
};

export const OutputApi = {
  generate: (conversionId: string, fmt: "csv" | "xlsx" = "csv") =>
    api.post<ConvertedOutput>(`/conversions/${conversionId}/generate-output`, null, { params: { fmt } }).then(r => r.data),
  list: (conversionId: string) =>
    api.get<ConvertedOutput[]>(`/conversions/${conversionId}/outputs`).then(r => r.data),
  preview: (conversionId: string, limit = 50) =>
    api.get<OutputPreview>(`/conversions/${conversionId}/output-preview`, { params: { limit } }).then(r => r.data),
  downloadUrl: (conversionId: string) => `/api/conversions/${conversionId}/download-output`,
};

export const LoadApi = {
  simulate: (conversionId: string) =>
    api.post<LoadRun>(`/conversions/${conversionId}/simulate-load`).then(r => r.data),
  runs: (conversionId: string) =>
    api.get<LoadRun[]>(`/conversions/${conversionId}/load-runs`).then(r => r.data),
  errors: (runId: string) => api.get<LoadError[]>(`/load-runs/${runId}/errors`).then(r => r.data),
  /** Errors from this conversion's most recent load run — convenience for the
   * Error Traceback drawer (no need to fetch run id separately). */
  latestErrors: (conversionId: string) =>
    api.get<LoadError[]>(`/conversions/${conversionId}/load-errors`).then(r => r.data),
  summary: (conversionId: string) =>
    api.get<LoadSummary>(`/conversions/${conversionId}/load-summary`).then(r => r.data),
};

export const WorkflowApi = {
  list: () => api.get<Workflow[]>("/workflows").then(r => r.data),
  get: (id: string) => api.get<Workflow>(`/workflows/${id}`).then(r => r.data),
  create: (body: any) => api.post<Workflow>("/workflows", body).then(r => r.data),
  update: (id: string, body: any) => api.put<Workflow>(`/workflows/${id}`, body).then(r => r.data),
  run: (id: string) => api.post<Workflow>(`/workflows/${id}/run`).then(r => r.data),
};

export const DependencyApi = {
  list: () => api.get<Dependency[]>("/dependencies").then(r => r.data),
  impact: (conversionId: string) =>
    api.get<{ object: string; dependencies: any[]; impacts: any[] }>(`/dependencies/impact/${conversionId}`).then(r => r.data),
};

export const DashboardApi = {
  kpis: () => api.get<DashboardKpis>("/dashboard/kpis").then(r => r.data),
};

export const LearningApi = {
  list: (params?: { kind?: string; category?: string }) =>
    api.get<LearnedMapping[]>("/learned-mappings", { params }).then(r => r.data),
  stats: () => api.get<LearningStats>("/learned-mappings/stats").then(r => r.data),
  capture: (body: Partial<LearnedMapping>) =>
    api.post<LearnedMapping>("/learned-mappings", body).then(r => r.data),
  delete: (id: string) => api.delete(`/learned-mappings/${id}`).then(r => r.data),
};

export const CutoverApi = {
  /** List environments configured for a project. */
  environments: (projectId: string) =>
    api.get<Environment[]>(`/projects/${projectId}/environments`).then(r => r.data),

  /** Idempotently seed the standard DEV/QA/UAT/PROD ladder. */
  seedDefaults: (projectId: string) =>
    api.post<Environment[]>(`/projects/${projectId}/environments/seed`).then(r => r.data),

  /** All environment runs for a conversion (DEV → QA → UAT → PROD progression). */
  runsForConversion: (conversionId: string) =>
    api.get<EnvironmentRun[]>(`/conversions/${conversionId}/environment-runs`).then(r => r.data),

  /** Promote a conversion into a new environment with a fresh dataset upload. */
  promote: (body: {
    environment_id: string;
    conversion_id: string;
    dataset_id?: string | null;
    notes?: string;
  }) =>
    api.post<EnvironmentRun>("/environment-runs", body).then(r => r.data),

  /** Update an environment run (status changes, notes, swap dataset). */
  updateRun: (runId: string, body: Partial<EnvironmentRun>) =>
    api.patch<EnvironmentRun>(`/environment-runs/${runId}`, body).then(r => r.data),

  /** The aggregate cutover dashboard (used by the Migration Monitor page). */
  dashboard: (projectId: string) =>
    api.get<CutoverDashboard>(`/projects/${projectId}/cutover`).then(r => r.data),
};
