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
  MappingCandidateGroup,
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
  delete: (id: string, cascade = false) =>
    api.delete<{ deleted: boolean; id: string; conversions_deleted?: number }>(
      `/datasets/${id}${cascade ? "?cascade=true" : ""}`
    ).then(r => r.data),
  preview: (id: string, limit = 50) =>
    api.get<DatasetPreview>(`/datasets/${id}/preview`, { params: { limit } }).then(r => r.data),
  suggestTemplate: (id: string) =>
    api.get<{ dataset_id: string; suggestions: TemplateSuggestion[] }>(`/datasets/${id}/suggest-template`).then(r => r.data),
  classify: (id: string) =>
    api.get<{
      dataset_id: string; signature: string; learned: boolean;
      source: { detected: string; candidates: { code: string; display: string; confidence: number; reason: string }[] };
      target: { detected_template_id: string | null; suggestions: TemplateSuggestion[] };
    }>(`/datasets/${id}/classify`).then(r => r.data),
  classifyLearn: (id: string, body: { source_system?: string; template_id?: string; target_object?: string }) =>
    api.post<{ learned: boolean; signature: string; id: string }>(`/datasets/${id}/classify-learn`, body).then(r => r.data),
  upload: (file: File, name?: string, description?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    if (description) fd.append("description", description);
    // Large (20-40 MB) files need well beyond the default 60s to upload + parse.
    return api.post<DatasetDetail>("/datasets/upload", fd, { timeout: 300_000 }).then(r => r.data);
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
    return api.post<FBDITemplateDetail>("/fbdi/upload", fd, { timeout: 300_000 }).then(r => r.data);
  },
  delete: (id: string) => api.delete(`/fbdi/templates/${id}`),
  reparse: (id: string) => api.post<FBDITemplateDetail>(`/fbdi/templates/${id}/reparse`).then(r => r.data),
  reparseAll: () => api.post<{ reparsed: number; results: Array<{ id: string; name: string; status: string; fields: number }> }>("/fbdi/reparse-all").then(r => r.data),
  seedStandardFields: (id: string) =>
    api.post<{ seeded: number; existing: number; schema_matched?: string; message: string }>(`/fbdi/templates/${id}/seed-standard-fields`).then(r => r.data),
  /** Which Oracle lookup types the templates need vs. which we hold codes for. */
  lookupStatus: () =>
    api.get<LookupStatus>("/fbdi/lookups/status").then(r => r.data),
  /** Import codes from a Manage Standard Lookups export (CSV/XLSX). */
  importLookups: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<LookupImportResult>("/fbdi/lookups/import", fd, { timeout: 120_000 })
      .then(r => r.data);
  },
};

/** A client-approved FBDI output held in the project-independent library. */
export interface GoldStandard {
  id: string;
  name: string;
  target_object?: string | null;
  template_id?: string | null;
  template_name?: string | null;
  file_name?: string | null;
  size: number;
  source_file_name?: string | null;
  match_confidence: number;
  rows: number;
  defaults_learned: number;
  suppressed_learned: number;
  mappings_learned: number;
  status: "learned" | "unmatched" | "error";
  note?: string | null;
  uploaded_by?: string | null;
  uploaded_at?: string | null;
  learned_at?: string | null;
}

/** An object whose gold rules are live but whose original file was never stored. */
export interface GoldOrphan {
  target_object: string;
  rules: number;
  defaults: number;
  suppressed: number;
  mappings: number;
  last_captured?: string | null;
}

export const GoldApi = {
  list: () =>
    api.get<{
      items: GoldStandard[];
      orphans: GoldOrphan[];
      summary: { gold_files: number; objects_covered: string[]; rules_from_gold: number };
    }>("/gold").then(r => r.data),
  upload: (file: File, opts: { name?: string; templateId?: string; sourceFile?: File } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    if (opts.sourceFile) fd.append("source_file", opts.sourceFile);
    if (opts.name) fd.append("name", opts.name);
    if (opts.templateId) fd.append("template_id", opts.templateId);
    return api.post<GoldStandard>("/gold/upload", fd, { timeout: 300_000 }).then(r => r.data);
  },
  relearn: (id: string) => api.post<GoldStandard>(`/gold/${id}/relearn`).then(r => r.data),
  remove: (id: string, purgeRules = false) =>
    api.delete<{ deleted: boolean; rules_purged: number }>(`/gold/${id}`, {
      params: { purge_rules: purgeRules },
    }).then(r => r.data),
  downloadUrl: (id: string) => `${api.defaults.baseURL}/gold/${id}/download`,
};

export interface LookupStatus {
  lookup_types: {
    lookup_type: string;
    codes: number;
    columns_using_it: number;
    status: "imported" | "missing";
  }[];
  summary: { referenced: number; imported: number; missing: number; total_codes: number };
}

export interface LookupImportResult {
  codes_imported: number;
  lookup_types: string[];
  fields_updated: number;
  types_matched: string[];
  types_not_used_by_any_template: string[];
  types_still_missing: string[];
}

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
  /** Materialize prerequisite edges from the current load sequence so the
   * dependency map shows what runs after what. */
  chainLoadOrder: (id: string) =>
    api.post<{ project_id: string; created: { source_object: string; target_object: string }[]; sequence: string[] }>(
      `/projects/${id}/chain-load-order`,
    ).then(r => r.data),
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
  switchProjectToEbs: (projectId: string) =>
    api.post<{ updated: number; message: string }>(
      `/conversions/project/${projectId}/use-ebs-source`
    ).then(r => r.data),
  /** Force-apply the stored gold reference standards to every conversion in the
   *  project (overrides AI-approved mappings; keeps human overrides). */
  applyReferenceStandards: (projectId: string) =>
    api.post<{ applied: number; objects: { conversion_id: string; target_object: string | null; applied: number }[] }>(
      `/conversions/project/${projectId}/apply-reference-standards`
    ).then(r => r.data),
  /** Force-apply the stored gold reference standard to one conversion. */
  applyReferenceStandard: (conversionId: string) =>
    api.post<{ conversion_id: string; target_object: string | null; applied: number }>(
      `/conversions/${conversionId}/apply-reference-standard`
    ).then(r => r.data),
  /** Catalog of conversion object types + the FBDI template set each needs. */
  objectTypes: () =>
    api.get<{ object_types: { key: string; label: string; step_count: number; steps: { label: string; load_order: number }[] }[] }>(
      "/conversions/object-types"
    ).then(r => r.data.object_types),
  /** One dataset -> all FBDI templates for the object type (Req 1). */
  generateSet: (body: { project_id: string; dataset_id: string; object_type: string }) =>
    api.post<{
      object_type: string;
      created: { label: string; template: string; conversion_id?: string; load_order: number }[];
      existing: { label: string; template: string; load_order: number }[];
      missing: { label: string; load_order: number }[];
      resolved_count: number;
      total_steps: number;
    }>("/conversions/generate-set", body).then(r => r.data),
  /** Phase 2 learning: derive source->target mappings + constant defaults from a
   *  populated example output, and/or apply a plain-text steering prompt. */
  learnFromExample: (conversionId: string, opts: { file?: File; prompt?: string }) => {
    const fd = new FormData();
    if (opts.file) fd.append("file", opts.file);
    if (opts.prompt) fd.append("prompt", opts.prompt);
    return api.post<{
      learned?: {
        target_object?: string; mapped_count: number; default_count: number; skipped: number;
        suppressed_count?: number; suppressed?: string[];
        mapped: { field: string; source: string; match: number }[];
        defaults: { field: string; value: string }[];
      };
      steer?: { applied: { field: string; source?: string; default?: string }[]; unmatched: string[] };
    }>(`/conversions/${conversionId}/learn-from-example`, fd, { timeout: 300_000 }).then(r => r.data);
  },
  /** Unified source columns for the Mapping Review canvas. Returns dataset
   *  profiles in dataset mode, or live Oracle EBS ALL_TAB_COLUMNS metadata
   *  when the conversion has no linked dataset (EBS live mode). */
  sourceColumns: (id: string) =>
    api.get<{ source_type: string; table: string | null; columns: import("@/types").DatasetColumnProfile[]; debug?: Record<string, any> | null }>(
      `/conversions/${id}/source-columns`
    ).then(r => r.data),
  /** Values Generate Output writes for unmapped target fields (control
   *  constants, sequence keys, learned + AI-inferred defaults). Used by the
   *  mapping-review UI to show "defaulted -> value" instead of a required gap. */
  effectiveDefaults: (id: string, useAi = true) =>
    api.get<{
      defaults: Record<string, string>;
      detail: { field: string; label: string; value: string; source: string }[];
      ai_used: boolean;
    }>(`/conversions/${id}/effective-defaults`, { params: { use_ai: useAi } }).then(r => r.data),
};

export interface ValueMapRecommendation {
  source_value: string;
  target_value: string;
  method: "exact_code" | "exact_meaning" | "synonym" | "fuzzy" | "learned" | "ai";
  confidence: number;
  already_valid?: boolean;
}

export interface ValueMapRecommendations {
  target_field: string;
  lov: { code: string; meaning?: string }[];
  default_if_blank?: string | null;
  source_column: string | null;
  distinct_values: string[];
  recommendations: ValueMapRecommendation[];
  unmatched: string[];
  coverage: number;
  error?: string;
}

/** One coded (LOV) target column, audited before generation. */
export interface CodedValueColumn {
  target_field: string;
  required: boolean;
  data_type?: string | null;
  source_column?: string | null;
  default_value?: string | null;
  lookup_type?: string | null;
  allowed_codes: { code: string; meaning: string }[];
  codes_source?: string | null;
  status: 'ok' | 'confirm' | 'error' | 'unverified';
  resolved: { from: string; to: string; how: string; confidence: number }[];
  unresolved: string[];
  message?: string | null;
  notes?: string | null;
}

export interface CodedValueAudit {
  columns: CodedValueColumn[];
  summary: {
    coded_columns?: number;
    ok?: number;
    confirm?: number;
    error?: number;
    unverified?: number;
    source_sampled?: boolean;
  };
}

export const MappingApi = {
  propagate: (mappingId: string) =>
    api.post<PropagationResult>(`/mappings/${mappingId}/propagate`).then(r => r.data),
  /** Audit of every coded/LOV column: what will be converted, what can't be grounded. */
  codedValues: (conversionId: string) =>
    api.get<CodedValueAudit>(`/conversions/${conversionId}/coded-values`).then(r => r.data),
  valueMapRecommendations: (mappingId: string) =>
    api.get<ValueMapRecommendations>(`/mappings/${mappingId}/value-map-recommendations`).then(r => r.data),
  acceptValueMap: (mappingId: string, body: {
    pairs: { source_value: string; target_value: string }[];
    default_value?: string | null;
  }) =>
    api.post<{ rule_id: string; pairs_applied: number; learned: number }>(
      `/mappings/${mappingId}/value-map-accept`, body
    ).then(r => r.data),
  propagationCandidates: (conversionId: string) =>
    api.get<PropagationCandidates>(`/conversions/${conversionId}/propagation-candidates`).then(r => r.data),
  suggest: (conversionId: string) =>
    api.post<MappingSuggestion[]>(`/conversions/${conversionId}/suggest-mapping`).then(r => r.data),
  /** Ranked alternative source-column candidates per target field. */
  candidates: (conversionId: string, opts?: { topN?: number; targetFieldId?: string }) =>
    api.get<MappingCandidateGroup[]>(`/conversions/${conversionId}/mapping-candidates`, {
      params: { top_n: opts?.topN ?? 5, target_field_id: opts?.targetFieldId },
    }).then(r => r.data),
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
  download: async (conversionId: string, filename = "output.csv") => {
    const response = await api.get(`/conversions/${conversionId}/download-output`, {
      responseType: "blob",
    });
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
  /** Generate + download every bound conversion's FBDI output for a project as
   * a single zip (files named/ordered by the supplier load sequence). */
  downloadAll: async (projectId: string, filename = "FBDI.zip", fmt: "csv" | "xlsx" = "csv") => {
    const response = await api.get(`/conversions/project/${projectId}/download-all`, {
      responseType: "blob",
      params: { fmt },
      timeout: 300000,
    });
    const url = window.URL.createObjectURL(new Blob([response.data], { type: "application/zip" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
  /** Package the ALREADY-generated outputs for a project into one zip (no
   * re-generation). Use after generating each object client-side. */
  downloadZip: async (projectId: string, filename = "FBDI.zip") => {
    const response = await api.get(`/conversions/project/${projectId}/download-zip`, {
      responseType: "blob",
      timeout: 120000,
    });
    const url = window.URL.createObjectURL(new Blob([response.data], { type: "application/zip" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
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

/** What the tool has learned about one Oracle object. */
export interface LearnedObjectGroup {
  target_object: string;
  total: number;
  kinds: { kind: string; label: string; count: number }[];
  sources: string[];
  last_captured?: string | null;
}

export const LearningApi = {
  list: (params?: {
    kind?: string; category?: string; project_id?: string;
    target_object?: string; q?: string; limit?: number;
  }) =>
    api.get<LearnedMapping[]>("/learned-mappings", { params }).then(r => r.data),
  /** Learnings grouped by the object they apply to — the way people actually look for them. */
  byObject: () =>
    api.get<{ objects: LearnedObjectGroup[]; total: number }>("/learned-mappings/by-object")
      .then(r => r.data),
  stats: (params?: { project_id?: string }) =>
    api.get<LearningStats>("/learned-mappings/stats", { params }).then(r => r.data),
  capture: (body: Partial<LearnedMapping>) =>
    api.post<LearnedMapping>("/learned-mappings", body).then(r => r.data),
  update: (id: string, body: Partial<LearnedMapping>) =>
    api.patch<LearnedMapping>(`/learned-mappings/${id}`, body).then(r => r.data),
  delete: (id: string) => api.delete(`/learned-mappings/${id}`).then(r => r.data),
  backfillProjects: () =>
    api.post("/learned-mappings/backfill-projects").then(r => r.data),
  /** Per-object gold reference standards stored in the DB (auto-applied to
   *  future conversions of the same object; no re-upload needed). */
  referenceStandards: () =>
    api.get<{ reference_standards: ReferenceStandard[] }>("/learned-mappings/reference-standards")
      .then(r => r.data.reference_standards),
  /** Summary of the seeded source→FBDI metadata mapping catalog. */
  catalogStatus: () =>
    api.get<CatalogStatus>("/learned-mappings/catalog-status").then(r => r.data),
  reseedCatalog: () =>
    api.post<{ seeded: number; skipped: number; total: number }>("/learned-mappings/reseed-catalog").then(r => r.data),
};

export type CatalogStatus = {
  total: number;
  by_source_system: { source_system: string; count: number }[];
  by_target_object: { target_object: string; count: number }[];
  rows: { source_system: string; target_object: string; source_field: string; fbdi_column: string; fbdi_sheet: string | null }[];
};

export type ReferenceStandard = {
  business_object: string;
  column_mappings: number;
  defaults: number;
  suppressions: number;
  captured_from: string | null;
  captured_at: string | null;
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

// ─────────────────────────────────────────────────────────────────
// v10 — Discovery (source connections + scan runs)
// ─────────────────────────────────────────────────────────────────

export const DiscoveryApi = {
  listConnections: (projectId?: string) =>
    api.get("/discovery/connections", { params: projectId ? { project_id: projectId } : {} }).then(r => r.data),

  createConnection: (body: Record<string, unknown>) =>
    api.post("/discovery/connections", body).then(r => r.data),

  getConnection: (id: string) =>
    api.get(`/discovery/connections/${id}`).then(r => r.data),

  deleteConnection: (id: string) =>
    api.delete(`/discovery/connections/${id}`),

  testConnection: (id: string) =>
    api.post(`/discovery/connections/${id}/test`).then(r => r.data),

  startRun: (connectionId: string, modules: string[] = []) =>
    api.post(`/discovery/connections/${connectionId}/runs`, { modules }).then(r => r.data),

  listRuns: (connectionId: string) =>
    api.get(`/discovery/connections/${connectionId}/runs`).then(r => r.data),

  listObjects: (runId: string) =>
    api.get(`/discovery/runs/${runId}/objects`).then(r => r.data),

  toggleObject: (objId: string, selected: boolean) =>
    api.patch(`/discovery/objects/${objId}/select`, null, { params: { selected } }).then(r => r.data),

  /**
   * Quick live COUNT(*) per EBS table — used by Setup Wizard scope step
   * to replace "pending scan" with real record volumes before project creation.
   */
  quickTableCounts: (body: {
    host: string;
    port: number;
    service_name: string;
    username: string;
    password: string;
    source_extracts: string[];
  }) =>
    api.post<{ counts: Record<string, number | null>; errors: Record<string, string> }>(
      "/discovery/quick-table-counts",
      body,
    ).then(r => r.data),

  /** Row counts from the most recent completed discovery run for a project. */
  scopeHints: (projectId: string) =>
    api.get<{ is_mock: boolean; run_id: string | null; table_counts: Record<string, number | null> }>(
      `/projects/${projectId}/discovery/scope-hints`,
    ).then(r => r.data),
};

// ─────────────────────────────────────────────────────────────────
// v10 — Audit log
// ─────────────────────────────────────────────────────────────────

export const AuditApi = {
  list: (params: { project_id?: string; conversion_id?: string; actor?: string; action?: string; limit?: number } = {}) =>
    api.get("/audit/events", { params }).then(r => r.data),

  create: (body: Record<string, unknown>) =>
    api.post("/audit/events", body).then(r => r.data),
};

// ─────────────────────────────────────────────────────────────────
// v10 — Chart of Accounts
// ─────────────────────────────────────────────────────────────────

export const CoaApi = {
  listStructures: (projectId: string) =>
    api.get("/coa/structures", { params: { project_id: projectId } }).then(r => r.data),

  createStructure: (body: Record<string, unknown>) =>
    api.post("/coa/structures", body).then(r => r.data),

  getStructure: (id: string) =>
    api.get(`/coa/structures/${id}`).then(r => r.data),

  listSegments: (structureId: string) =>
    api.get(`/coa/structures/${structureId}/segments`).then(r => r.data),

  createSegment: (body: Record<string, unknown>) =>
    api.post("/coa/segments", body).then(r => r.data),

  listCrosswalks: (segmentId: string, status?: string) =>
    api.get(`/coa/segments/${segmentId}/crosswalks`, { params: status ? { status } : {} }).then(r => r.data),

  createCrosswalk: (body: Record<string, unknown>) =>
    api.post("/coa/crosswalks", body).then(r => r.data),

  updateCrosswalk: (id: string, body: Record<string, unknown>) =>
    api.patch(`/coa/crosswalks/${id}`, body).then(r => r.data),

  stats: (structureId: string) =>
    api.get(`/coa/structures/${structureId}/stats`).then(r => r.data),
};

// ─────────────────────────────────────────────────────────────────
// v10 — Governance (issues, risks, sign-offs, rehearsals, recon)
// ─────────────────────────────────────────────────────────────────

export const GovernanceApi = {
  // Issues
  listIssues: (projectId: string, status?: string) =>
    api.get("/governance/issues", { params: { project_id: projectId, ...(status ? { status } : {}) } }).then(r => r.data),
  createIssue: (body: Record<string, unknown>) =>
    api.post("/governance/issues", body).then(r => r.data),
  updateIssue: (id: string, body: Record<string, unknown>) =>
    api.patch(`/governance/issues/${id}`, body).then(r => r.data),
  deleteIssue: (id: string) =>
    api.delete(`/governance/issues/${id}`),

  // Risks
  listRisks: (projectId: string, status?: string) =>
    api.get("/governance/risks", { params: { project_id: projectId, ...(status ? { status } : {}) } }).then(r => r.data),
  createRisk: (body: Record<string, unknown>) =>
    api.post("/governance/risks", body).then(r => r.data),
  updateRisk: (id: string, body: Record<string, unknown>) =>
    api.patch(`/governance/risks/${id}`, body).then(r => r.data),
  deleteRisk: (id: string) =>
    api.delete(`/governance/risks/${id}`),

  // Sign-offs
  listSignOffs: (projectId: string) =>
    api.get("/governance/sign-offs", { params: { project_id: projectId } }).then(r => r.data),
  createSignOff: (body: Record<string, unknown>) =>
    api.post("/governance/sign-offs", body).then(r => r.data),
  updateSignOff: (id: string, body: Record<string, unknown>) =>
    api.patch(`/governance/sign-offs/${id}`, body).then(r => r.data),

  // Dress rehearsals
  listRehearsals: (projectId: string) =>
    api.get("/governance/rehearsals", { params: { project_id: projectId } }).then(r => r.data),
  createRehearsal: (body: Record<string, unknown>) =>
    api.post("/governance/rehearsals", body).then(r => r.data),
  updateRehearsal: (id: string, body: Record<string, unknown>) =>
    api.patch(`/governance/rehearsals/${id}`, body).then(r => r.data),

  // Cutover tasks
  listTasks: (projectId: string, rehearsalId?: string) =>
    api.get("/governance/cutover-tasks", { params: { project_id: projectId, ...(rehearsalId ? { rehearsal_id: rehearsalId } : {}) } }).then(r => r.data),
  createTask: (body: Record<string, unknown>) =>
    api.post("/governance/cutover-tasks", body).then(r => r.data),
  updateTask: (id: string, body: Record<string, unknown>) =>
    api.patch(`/governance/cutover-tasks/${id}`, body).then(r => r.data),

  // Reconciliation
  listRecon: (projectId: string, conversionId?: string) =>
    api.get("/governance/reconciliation", { params: { project_id: projectId, ...(conversionId ? { conversion_id: conversionId } : {}) } }).then(r => r.data),
  createRecon: (body: Record<string, unknown>) =>
    api.post("/governance/reconciliation", body).then(r => r.data),

  reconSummary: (projectId: string) =>
    api.get(`/governance/summary/${projectId}`).then(r => r.data),
};

// === Source Systems / Fusion Modules (Setup Wizard) ===========================
export const SourceSystemsApi = {
  list: () => api.get<any[]>("/source-systems").then(r => r.data),
};

export const FusionModulesApi = {
  list: () => api.get<any[]>("/fusion-modules").then(r => r.data),
};

export const SourceConnectionsApi = {
  list: () => api.get<any[]>("/source-connections").then(r => r.data),
  create: (body: Record<string, unknown>) =>
    api.post<any>("/source-connections", body).then(r => r.data),
  get: (id: string) => api.get<any>(`/source-connections/${id}`).then(r => r.data),
  update: (id: string, body: Record<string, unknown>) =>
    api.patch<any>(`/source-connections/${id}`, body).then(r => r.data),
  test: (id: string) =>
    api.post<any>(`/source-connections/${id}/test`).then(r => r.data),
  remove: (id: string) => api.delete(`/source-connections/${id}`).then(r => r.data),
  discoverScope: (id: string) =>
    api.post<any>(`/source-connections/${id}/discover-scope`).then(r => r.data),
};

// === Oracle Fusion Cloud target (Load to Fusion) ==============================
export const FusionApi = {
  getConnection: () =>
    api.get<{ id: string | null; base_url: string | null; username: string | null; has_credentials: boolean; last_test_ok: boolean | null; last_tested_at: string | null; last_test_error: string | null }>("/fusion/connection").then(r => r.data),
  saveConnection: (body: { base_url: string; username: string; password?: string; name?: string }) =>
    api.post("/fusion/connection", body).then(r => r.data),
  testConnection: (body?: { base_url?: string; username?: string; password?: string }) =>
    api.post<{ ok: boolean; status: number | null; message: string }>("/fusion/connection/test", body ?? {}).then(r => r.data),
  targets: (conversionId: string) =>
    api.get<{ business_object: string | null; interface_tables: string[]; loadable: boolean; work_area: string | null; pod_url: string | null }>(`/conversions/${conversionId}/fusion-targets`).then(r => r.data),
  load: (conversionId: string) =>
    api.post<{ ok: boolean; status: number | null; message: string; request_id: string | null; rows: number; load_run_id: string }>(`/conversions/${conversionId}/load-to-fusion`).then(r => r.data),
  loadStatus: (runId: string) =>
    api.get<{ ok: boolean; state: string; raw: string | null; request_id: string | null; message: string; http_status?: number }>(`/fusion/load-runs/${runId}/status`).then(r => r.data),
  preflight: (conversionId: string) =>
    api.get<{ ok: boolean; level: string; resource: string; http_status: number | null; message: string; business_object: string | null }>(`/conversions/${conversionId}/fusion-preflight`).then(r => r.data),
};

// === Copilot ===================================================================
export const CopilotApi = {
  ask: (body: { project_id: string; messages: { role: string; content: string }[] }) =>
    api.post<{ answer: string; citations: string[] }>("/copilot/ask", body).then(r => r.data),
  suggestDefault: (body: { column_name: string; samples?: any[]; null_percent?: number; target_field?: string; target_data_type?: string }) =>
    api.post<{ suggestion: string; available: boolean; reason?: string }>("/copilot/suggest-default", body).then(r => r.data),
};

// === App settings — Anthropic model selector (cost vs capability) =============
export interface AiModelOption { id: string; label: string; tier: string; }
export interface AiModelSetting { current: string; options: AiModelOption[]; }
export const SettingsApi = {
  getAiModel: () => api.get<AiModelSetting>("/settings/ai-model").then(r => r.data),
  setAiModel: (model: string) =>
    api.put<AiModelSetting>("/settings/ai-model", { model }).then(r => r.data),
};

// === Inherited Reference Standards ============================================
export interface InheritedStandard {
  target_field: string;
  master_object: string;
  rule_type: string;
  rule_config: Record<string, any>;
  captured_from: string;
  originated_in_project_id: string | null;
}

export const InheritedStandardsApi = {
  forConversion: (conversionId: string) =>
    api.get<InheritedStandard[]>(`/conversions/${conversionId}/inherited-standards`).then(r => r.data),
};

// === Slice6 (readiness / safeguards / exec-summary) ===========================
export const Slice6Api = {
  safeguards: (projectId: string) =>
    api.get<any>(`/projects/${projectId}/safeguards`).then(r => r.data),
  readiness: (projectId: string) =>
    api.get<any>(`/projects/${projectId}/readiness`).then(r => r.data),
  execSummary: (projectId: string) =>
    api.get<any>(`/projects/${projectId}/exec-summary`).then(r => r.data),
  reconciliation: (projectId: string) =>
    api.get<any[]>(`/projects/${projectId}/reconciliation`).then(r => r.data),
  seedReconciliation: (projectId: string) =>
    api.post<any[]>(`/projects/${projectId}/reconciliation/seed`).then(r => r.data),
  runbook: (projectId: string) =>
    api.get<any[]>(`/projects/${projectId}/runbook`).then(r => r.data),
  seedRunbook: (projectId: string, force = false) =>
    api.post<any[]>(`/projects/${projectId}/runbook/seed`, null, { params: { force } }).then(r => r.data),
  updateRunbookTask: (taskId: string, body: Record<string, unknown>) =>
    api.patch<any>(`/runbook-tasks/${taskId}`, body).then(r => r.data),
};

// === COAApi alias (some pages import COAApi, others import CoaApi) =============
export { CoaApi as COAApi };
