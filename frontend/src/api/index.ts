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

// ─── Clients (tenants) ───
export interface ClientSummary {
  id: string;
  name: string;
  code?: string | null;
  description?: string | null;
  is_default: boolean;
  active: boolean;
  counts: { learnings: number; gold: number; projects: number; templates: number };
}
export const ClientsApi = {
  list: () =>
    api.get<{ clients: ClientSummary[]; global: { learnings: number; templates: number } }>("/clients")
      .then(r => r.data),
  create: (body: { name: string; code?: string; description?: string }) =>
    api.post<ClientSummary>("/clients", body).then(r => r.data),
  update: (
    id: string,
    body: Partial<{ name: string; code: string; description: string; active: boolean; is_default: boolean }>,
  ) => api.patch<ClientSummary>(`/clients/${id}`, body).then(r => r.data),
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
  /** Source-data anomaly / outlier detection (pre-mapping). */
  anomalies: (id: string, opts?: { useAi?: boolean }) =>
    api.get<{
      dataset_id: string; dataset_name?: string; rows: number; columns_scanned: number;
      duplicate_rows: number; ai_used?: boolean;
      summary: { error: number; warning: number; info: number; columns_flagged: number };
      findings: { column: string; issue_type: string; severity: "error" | "warning" | "info";
        count: number; pct: number; examples: string[]; detail: string; ai_risk?: string }[];
    }>(`/datasets/${id}/anomalies`, { params: { use_ai: opts?.useAi ?? false }, timeout: 90000 }).then(r => r.data),
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
  /** Move Customer conversions off a flat template onto the real 19-sheet Customer Import. */
  repointCustomer: (deleteFlat = false) =>
    api.post<{
      real_template: { id: string; name: string; sheets: number };
      flat_templates_found: number;
      conversions_repointed: number;
      mappings_regenerated: number;
      flat_templates_deleted: number;
      seeded_real_template?: boolean;
    }>("/fbdi/customer/repoint", null, { params: { delete_flat: deleteFlat } }).then(r => r.data),
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

/** Per-file outcome of a (possibly multi-file) gold upload. */
export interface GoldUploadResult {
  items: (GoldStandard | { file_name: string; status: "error"; note: string })[];
  uploaded: number;
  unmatched: number;
  failed: number;
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
  /** Upload one or many gold files. A supplier load is six of them, so batch is the norm.
   *  `sourceFile` is a single extract shared by the whole batch — which is exactly the
   *  fan-out case: one legacy supplier extract produced all six outputs. */
  upload: (
    files: File[],
    opts: { name?: string; templateId?: string; sourceFile?: File } = {},
  ) => {
    const fd = new FormData();
    files.forEach(f => fd.append("files", f));
    if (opts.sourceFile) fd.append("source_file", opts.sourceFile);
    // Name/template only make sense for a single file — in a batch each file
    // names itself and detects its own template.
    if (files.length === 1 && opts.name) fd.append("name", opts.name);
    if (files.length === 1 && opts.templateId) fd.append("template_id", opts.templateId);
    return api.post<GoldUploadResult>("/gold/upload", fd, { timeout: 600_000 })
      .then(r => r.data);
  },
  /** Fix a wrongly-detected template (or rename). Changing the template re-learns the
   *  file and re-keys its rules to the new object — a wrong template means the rules
   *  were being applied to the wrong conversions, so this is not a cosmetic edit. */
  patch: (id: string, body: { name?: string; template_id?: string }) =>
    api.patch<GoldStandard & {
      change: {
        retargeted: boolean; from_object?: string; to_object?: string;
        rules_retired?: number; defaults?: number; suppressed?: number; mappings?: number;
      };
    }>(`/gold/${id}`, body).then(r => r.data),
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
  /** Source file(s) -> all FBDI templates for the object type. Accepts a single
   *  dataset_id or multiple dataset_ids (priority order) for a merged output. */
  generateSet: (body: { project_id: string; dataset_id?: string; dataset_ids?: string[]; object_type: string }) =>
    api.post<{
      object_type: string;
      created: { label: string; template: string; conversion_id?: string; load_order: number }[];
      existing: { label: string; template: string; load_order: number }[];
      missing: { label: string; load_order: number }[];
      resolved_count: number;
      total_steps: number;
    }>("/conversions/generate-set", body).then(r => r.data),
  /** Manage a conversion's multi-source list (priority order). mode: append | replace. */
  setSources: (conversionId: string, datasetIds: string[], mode: "append" | "replace" = "append") =>
    api.put<Conversion>(`/conversions/${conversionId}/sources`,
      { dataset_ids: datasetIds, mode }).then(r => r.data),
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
  /** Remove learned/gold-derived constant defaults (e.g. a wrong Country → AE that a
   *  gold file baked in). Forgets the object-level rule so it can't reapply, and can
   *  re-run AI to re-fill the freed fields. Control defaults are never touched. */
  resetDefaults: (
    id: string,
    body: { fields?: string[]; include_ai_defaults?: boolean; forget_global?: boolean; rerun_ai?: boolean } = {},
  ) =>
    api.post<{
      target_object: string;
      rules_forgotten: number;
      defaults_cleared: number;
      fields: string[];
      remapped: number | null;
    }>(`/conversions/${id}/reset-defaults`, body).then(r => r.data),
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
  /** On-demand AI verdict + reason for uncertain candidates. Returns groups plus
   *  an ai map keyed target_field_id -> source_column -> {verdict, reason}.
   *  targetFieldIds scopes the pass to the rows on screen so a wide template stays
   *  fast; a cold Render backend is retried once (POSTs are not auto-retried). */
  vetCandidates: async (
    conversionId: string,
    opts?: { topN?: number; onlyUncertain?: boolean; targetFieldIds?: string[]; force?: boolean },
  ) => {
    const call = () => api.post<{
      groups: MappingCandidateGroup[];
      ai: Record<string, Record<string, { verdict: string; reason: string }>>;
      vetted: number; reused?: number; sent: number; eligible?: number;
      capped?: boolean; already_done?: boolean;
    }>(
      `/conversions/${conversionId}/vet-candidates`,
      { target_field_ids: opts?.targetFieldIds, force: opts?.force },
      { params: { top_n: opts?.topN ?? 4, only_uncertain: opts?.onlyUncertain ?? true }, timeout: 120_000 },
    ).then(r => r.data);
    try {
      return await call();
    } catch (e: any) {
      // wake a sleeping free-tier backend and try once more
      const s = e?.response?.status;
      if (!e?.response || s === 502 || s === 503 || s === 504) {
        await new Promise((r) => setTimeout(r, 3500));
        return await call();
      }
      throw e;
    }
  },
  /** Opt-in AI fill for unmapped target fields (reviewable 'suggested' mappings). */
  aiFillBlanks: (conversionId: string, opts?: { requiredOnly?: boolean; targetFieldIds?: string[] }) =>
    api.post<{ filled: number; considered: number; note?: string }>(
      `/conversions/${conversionId}/ai-fill-blanks`,
      { target_field_ids: opts?.targetFieldIds },
      { params: { required_only: opts?.requiredOnly ?? false }, timeout: 180_000 },
    ).then(r => r.data),
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
  // Plain-English → structured rule (the "Describe this rule in plain English" box).
  translateRule: (
    conversionId: string,
    body: {
      description: string;
      target_field_id?: string;
      source_column?: string;
      sample_size?: number;
    }
  ) =>
    api
      .post<{
        rule_type: string;
        config: any;
        explanation?: string;
        ambiguities?: { phrase: string; interpreted_as: string; alternatives: string[] }[];
        source?: "local" | "ai";
      }>(`/conversions/${conversionId}/rules/translate`, body)
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

export interface DqReport {
  error_count: number;
  warning_count: number;
  hard_error_count: number;
  blocked: boolean;
  cleansing_fix_count: number;
  cleansing_fixes: { field: string; rule: string; count: number }[];
  top_issues: { field_name?: string; issue_type?: string; severity?: string; message?: string }[];
}

export interface DqRule {
  id: string;
  kind: "validation" | "cleansing";
  target_object: string;
  field?: string | null;
  rule_type: string;
  params: Record<string, any>;
  severity: string;
  description?: string | null;
  source: string;
  active: boolean;
  is_global: boolean;
  client_id?: string | null;
}

export const OutputApi = {
  /** Kick off generation (async by default) — returns immediately with status.
   *  includeHeader: undefined = auto (supplier headerless, others with header);
   *  true/false forces the column-label row on/off. */
  generate: (conversionId: string, fmt: "csv" | "xlsx" | "template" = "csv", includeHeader?: boolean) =>
    api.post<{ status: string; conversion_id: string }>(
      `/conversions/${conversionId}/generate-output`, null,
      { params: { fmt, ...(includeHeader === undefined ? {} : { include_header: includeHeader }) } },
    ).then(r => r.data),
  generationStatus: (conversionId: string) =>
    api.get<{
      status: "idle" | "generating" | "ready" | "failed";
      error?: string | null;
      output?: { id: string; file_name: string; row_count: number; column_count: number;
                 dq_report?: DqReport | null } | null;
    }>(`/conversions/${conversionId}/generation-status`).then(r => r.data),
  /** Start generation, then poll until it's ready or fails. Returns the artifact
   *  info on success; throws with the backend error message on failure. Heavy
   *  multi-sheet objects (Customer/Item) build in the background this way so the
   *  request can't hit the gateway timeout. */
  generateAndWait: async (
    conversionId: string,
    fmt: "csv" | "xlsx" | "template" = "csv",
    onTick?: (elapsedSec: number) => void,
    includeHeader?: boolean,
  ): Promise<{ id: string; file_name: string; row_count: number; column_count: number; dq_report?: DqReport | null }> => {
    await api.post(`/conversions/${conversionId}/generate-output`, null,
      { params: { fmt, ...(includeHeader === undefined ? {} : { include_header: includeHeader }) } });
    const start = Date.now();
    // Poll up to ~8 minutes; generation of a 19-sheet object on a small instance
    // can take a while, but it's off the request thread so nothing times out.
    for (let i = 0; i < 160; i++) {
      await new Promise(res => setTimeout(res, 3000));
      const s = await OutputApi.generationStatus(conversionId);
      onTick?.(Math.round((Date.now() - start) / 1000));
      if (s.status === "ready" && s.output) return s.output;
      if (s.status === "failed") throw new Error(s.error || "Generation failed");
    }
    throw new Error("Generation is taking unusually long — check back shortly.");
  },
  list: (conversionId: string) =>
    api.get<ConvertedOutput[]>(`/conversions/${conversionId}/outputs`).then(r => r.data),
  preview: (conversionId: string, limit = 50) =>
    api.get<OutputPreview>(`/conversions/${conversionId}/output-preview`, { params: { limit } }).then(r => r.data),
  /** Kick off merged generation (async) — returns the CARRIER conversion id to poll. */
  generateMerged: (conversionId: string, includeHeader?: boolean) =>
    api.post<{ status: string; conversion_id: string; carrier_id?: string }>(
      `/conversions/${conversionId}/generate-merged`, null,
      { params: { ...(includeHeader === undefined ? {} : { include_header: includeHeader }) } },
    ).then(r => r.data),
  /** Merged generate, then poll the carrier conversion until ready. Returns the
   *  carrier's artifact info (file_name lives under conversion_id = carrier). */
  generateMergedAndWait: async (
    conversionId: string, includeHeader?: boolean, onTick?: (sec: number) => void,
  ): Promise<{ conversion_id: string; file_name: string; row_count: number;
    column_count: number; dq_report?: DqReport | null }> => {
    const start = await OutputApi.generateMerged(conversionId, includeHeader);
    const carrier = start.conversion_id;
    const t0 = Date.now();
    for (let i = 0; i < 160; i++) {
      await new Promise(res => setTimeout(res, 3000));
      const s = await OutputApi.generationStatus(carrier);
      onTick?.(Math.round((Date.now() - t0) / 1000));
      if (s.status === "ready" && s.output) return { conversion_id: carrier, ...s.output };
      if (s.status === "failed") throw new Error(s.error || "Merged generation failed");
    }
    throw new Error("Merged generation is taking unusually long — check back shortly.");
  },
  /** Kick off merged generation for EVERY interface object in a project, in the
   *  background. Returns one carrier conversion per object to poll. */
  generateMergedAll: (projectId: string, fmt: "csv" | "xlsx" | "template" = "csv", includeHeader?: boolean) =>
    api.post<{ status: string; objects: number;
      carriers: { object: string; conversion_id: string }[] }>(
      `/conversions/project/${projectId}/generate-merged-all`, null,
      { params: { fmt, ...(includeHeader === undefined ? {} : { include_header: includeHeader }) } },
    ).then(r => r.data),
  /** Generate every interface's merged file in the background, poll all carriers
   *  until each is ready/failed, then return per-object results. */
  generateMergedAllAndWait: async (
    projectId: string, fmt: "csv" | "xlsx" | "template" = "csv", includeHeader?: boolean,
    onTick?: (sec: number, done: number, total: number) => void,
  ): Promise<{ object: string; ready: boolean; error?: string }[]> => {
    const start = await OutputApi.generateMergedAll(projectId, fmt, includeHeader);
    const carriers = start.carriers || [];
    const total = carriers.length;
    const done: Record<string, { ready: boolean; error?: string }> = {};
    const t0 = Date.now();
    for (let i = 0; i < 240; i++) {
      await new Promise(res => setTimeout(res, 3000));
      for (const c of carriers) {
        if (done[c.conversion_id]) continue;
        const s = await OutputApi.generationStatus(c.conversion_id);
        if (s.status === "ready") done[c.conversion_id] = { ready: true };
        else if (s.status === "failed") done[c.conversion_id] = { ready: false, error: s.error || "failed" };
      }
      const nDone = Object.keys(done).length;
      onTick?.(Math.round((Date.now() - t0) / 1000), nDone, total);
      if (nDone >= total) break;
    }
    return carriers.map(c => ({
      object: c.object,
      ready: !!done[c.conversion_id]?.ready,
      error: done[c.conversion_id]?.error,
    }));
  },
  /** Preview the MERGED output for this conversion's interface (all sources). */
  mergedPreview: (conversionId: string, limit = 50) =>
    api.get<{ columns: string[]; rows: Record<string, any>[]; total_rows: number;
      sources: string[]; target_object?: string }>(
      `/conversions/${conversionId}/merged-preview`, { params: { limit } }).then(r => r.data),
  /** Predictive pre-load DQ report (what Oracle will reject + how to fix). */
  preloadReport: (conversionId: string, sampleRows = 3000) =>
    api.get<DqReport & { explanations: { issue_type: string; count: number; meaning: string; fix: string }[];
      sampled_rows: number; target_object: string }>(
      `/conversions/${conversionId}/preload-report`, { params: { sample_rows: sampleRows } }).then(r => r.data),
  /** Source vs merged-output vs load reconciliation. */
  reconciliation: (conversionId: string) =>
    api.get<{ sources: { name: string; rows: number }[]; source_total: number; output_rows: number | null;
      merged_or_deduped: number | null; load: any; narrative: string }>(
      `/conversions/${conversionId}/reconciliation`).then(r => r.data),
  /** Fuzzy duplicate / entity resolution over the merged interface data — clusters
   *  of records likely to be the same entity despite non-identical keys/names. */
  duplicateCandidates: (conversionId: string, opts?: { threshold?: number; useAi?: boolean }) =>
    api.get<{
      object?: string; rows_scanned: number; identity_fields: string[]; anchor?: string;
      cluster_count?: number; duplicate_rows?: number; truncated?: boolean; note?: string;
      ai_used?: boolean; sources?: string[];
      clusters: { confidence: number; size: number; fields: string[];
        verdict?: string; ai_reason?: string;
        members: { row: number; values: Record<string, any> }[] }[];
    }>(`/conversions/${conversionId}/duplicate-candidates`, {
      params: { threshold: opts?.threshold ?? 0.86, use_ai: opts?.useAi ?? false },
      timeout: 120000,
    }).then(r => r.data),
  /** Per-source converted preview (multi-source): one block per source file. */
  previewBySource: (conversionId: string, limit = 50) =>
    api.get<{ multi: boolean; sources: { source_id: string | null; source_name: string | null;
      columns: string[]; rows: Record<string, any>[]; total_rows: number }[] }>(
      `/conversions/${conversionId}/output-preview-by-source`, { params: { limit } }).then(r => r.data),
  downloadUrl: (conversionId: string) => `/api/conversions/${conversionId}/download-output`,
  download: async (conversionId: string, filename = "output.csv") => {
    const response = await api.get(`/conversions/${conversionId}/download-output`, {
      responseType: "blob",
    });
    // ALWAYS honour the server's real filename/extension — a supplier FBDI output
    // is a .zip (Oracle bundle), not .csv. Saving zip bytes under a forced .csv
    // name makes Excel try to "recover" a corrupt workbook. The passed name is a
    // fallback only; the Content-Disposition filename wins when present.
    const cd = (response.headers?.["content-disposition"] as string) || "";
    const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd);
    const serverName = m ? decodeURIComponent(m[1].replace(/"/g, "").trim()) : "";
    const type = (response.headers?.["content-type"] as string) || "";
    const url = window.URL.createObjectURL(new Blob([response.data], type ? { type } : undefined));
    const a = document.createElement("a");
    a.href = url;
    a.download = serverName || filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
  /** Download every interface's MERGED FBDI file as one zip — one merged,
   *  de-duplicated, cleansed, validated file per interface (not one per source).
   *  Generates all merged files in the BACKGROUND first (so wide multi-source
   *  objects don't hit the gateway timeout), polls until ready, then the zip is a
   *  fast reuse of the already-generated files. */
  downloadAll: async (
    projectId: string, filename = "FBDI.zip", fmt: "csv" | "xlsx" | "template" = "csv",
    onTick?: (sec: number, done: number, total: number) => void,
  ) => {
    // 1) Build every merged interface file in the background, wait for all.
    const results = await OutputApi.generateMergedAllAndWait(projectId, fmt, undefined, onTick);
    // 2) Fast zip — reuses the files just generated (regenerate=false).
    const response = await api.get(`/conversions/project/${projectId}/download-all`, {
      responseType: "blob",
      params: { fmt },
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
    return results;
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

/** Per-file outcome of a mapping-workbook import. */
export interface MappingImportResult {
  files: {
    file_name: string;
    error?: string;
    imported?: number;
    updated?: number;
    skipped?: number;
    objects?: string[];
    unresolved_object?: string[];
    columns_detected?: {
      source?: string | null; target?: string | null;
      object?: string | null; source_system?: string | null;
    };
  }[];
  imported: number;
  updated: number;
  skipped: number;
}

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
    target_object?: string; client_id?: string; q?: string; limit?: number;
  }) =>
    api.get<LearnedMapping[]>("/learned-mappings", { params }).then(r => r.data),
  /** Learnings grouped by the object they apply to — the way people actually look for them.
   *  Optionally scoped to a client id (or the literal "global"). */
  byObject: (clientId?: string) =>
    api.get<{ objects: LearnedObjectGroup[]; total: number }>("/learned-mappings/by-object",
      { params: clientId ? { client_id: clientId } : undefined })
      .then(r => r.data),
  /** Every object a mapping row can be keyed to: canonical Oracle FBDI objects +
   *  loaded templates + already-learned objects. Used to populate the object picker. */
  knownObjects: () =>
    api.get<{ objects: string[]; grouped: Record<string, string[]> }>(
      "/learned-mappings/known-objects"
    ).then(r => r.data),
  /** Import one or more source→target mapping workbooks as reusable column mappings. */
  importMappings: (
    files: File[],
    opts: { defaultObject?: string; sourceSystem?: string } = {},
  ) => {
    const fd = new FormData();
    files.forEach(f => fd.append("files", f));
    if (opts.defaultObject) fd.append("default_object", opts.defaultObject);
    if (opts.sourceSystem) fd.append("source_system", opts.sourceSystem);
    return api.post<MappingImportResult>("/learned-mappings/import-mappings", fd, {
      timeout: 300_000,
    }).then(r => r.data);
  },
  /** Analyse mapping documents into a reviewable proposal. Writes nothing until applied. */
  analyzeProposal: (
    files: File[],
    opts: { clientId?: string; targetObject?: string; sourceSystem?: string } = {},
  ) => {
    const fd = new FormData();
    files.forEach(f => fd.append("files", f));
    if (opts.clientId) fd.append("client_id", opts.clientId);
    if (opts.targetObject) fd.append("target_object", opts.targetObject);
    if (opts.sourceSystem) fd.append("source_system", opts.sourceSystem);
    return api.post<{ proposals: any[] }>("/mapping-proposals/analyze", fd, {
      timeout: 300_000,
    }).then(r => r.data);
  },
  listProposals: (params?: { status?: string; client_id?: string }) =>
    api.get<any[]>("/mapping-proposals", { params }).then(r => r.data),
  getProposal: (id: string) =>
    api.get<any>(`/mapping-proposals/${id}`).then(r => r.data),
  decideProposal: (id: string, decision: string, rowNos?: number[]) =>
    api.post<{ updated: number }>(`/mapping-proposals/${id}/decide`,
      { decision, row_nos: rowNos }).then(r => r.data),
  vetProposal: (id: string) =>
    api.post<any>(`/mapping-proposals/${id}/vet`, {}, { timeout: 180_000 }).then(r => r.data),
  overrideProposalRow: (id: string, rowNo: number, source: string, reason: string) =>
    api.post<any>(`/mapping-proposals/${id}/override`,
      { row_no: rowNo, source, reason }).then(r => r.data),
  applyProposal: (id: string) =>
    api.post<{ learnings_written: number; conversions_touched: number; objects: string[] }>(
      `/mapping-proposals/${id}/apply`, {}, { timeout: 300_000 }).then(r => r.data),
  discardProposal: (id: string) =>
    api.delete(`/mapping-proposals/${id}`).then(r => r.data),
  // ── Manual template mapper ──────────────────────────────────────────────
  manualContext: (targetObject: string, templateId?: string, clientId?: string, proposalId?: string) =>
    api.get<{ target_object: string; learnt_count: number; gold_count: number; doc_count?: number;
      fields: Array<{ target_field: string; sheet_name?: string | null; required: boolean;
        learnt_source?: string | null; learnt_from?: string | null; learnt_rule?: string | null;
        gold_source?: string | null; doc_source?: string | null }> }>(
      "/manual-map/context",
      { params: { target_object: targetObject, template_id: templateId, client_id: clientId, proposal_id: proposalId } },
    ).then(r => r.data),
  manualVet: (pairs: Array<{ target_field: string; source_field: string }>, useAi = false) =>
    api.post<{ results: Array<{ target_field: string; source_field: string; plausible: boolean;
      reason: string; ai_verdict?: string; ai_reason?: string }> }>(
      "/manual-map/vet", { pairs, use_ai: useAi }, { timeout: useAi ? 120_000 : 30_000 },
    ).then(r => r.data),
  manualSources: (clientId?: string, targetObject?: string) =>
    api.get<{ sources: string[] }>("/manual-map/sources",
      { params: { client_id: clientId, target_object: targetObject } }).then(r => r.data),
  manualSuggestFill: (targetFields: string[], sources: string[]) =>
    api.post<{ filled: Array<{ target_field: string; source: string; reason: string }>; considered: number }>(
      "/manual-map/suggest-fill", { target_fields: targetFields, sources },
      { timeout: 180_000 }).then(r => r.data),
  manualSuggestOneToMany: (source: string, targetFields: string[]) =>
    api.post<{ source: string; matches: Array<{ target_field: string; reason: string }> }>(
      "/manual-map/suggest", { mode: "one_to_many", source, target_fields: targetFields },
      { timeout: 120_000 }).then(r => r.data),
  manualSuggestManyToOne: (targetField: string, sources: string[]) =>
    api.post<{ target_field: string; use: string[]; rule_type?: string; separator?: string; reason?: string }>(
      "/manual-map/suggest", { mode: "many_to_one", target_field: targetField, sources },
      { timeout: 120_000 }).then(r => r.data),
  manualSave: (body: { client_id?: string; target_object: string; source_system?: string;
    rows: Array<{ target_field: string; source_field: string; rule_type?: string; separator?: string; reason?: string; clear?: boolean }> }) =>
    api.post<{ saved: number; updated: number; cleared: number; conversions_touched: number }>(
      "/manual-map/save", body, { timeout: 180_000 }).then(r => r.data),
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

// === Validation & Cleansing rules (scoped by FBDI object + client) ============
export const DqRulesApi = {
  list: (params?: { target_object?: string; kind?: string; client_id?: string }) =>
    api.get<{ rules: DqRule[]; count: number }>("/dq-rules", { params }).then(r => r.data),
  create: (body: Partial<DqRule> & { kind: string; target_object: string; rule_type: string }) =>
    api.post<DqRule>("/dq-rules", body).then(r => r.data),
  update: (id: string, body: Partial<DqRule>) =>
    api.patch<DqRule>(`/dq-rules/${id}`, body).then(r => r.data),
  remove: (id: string) => api.delete(`/dq-rules/${id}`).then(r => r.data),
  extract: (body: { target_object: string; template_id: string; client_id?: string }) =>
    api.post<{ target_object: string; created: number; skipped: number }>(
      "/dq-rules/extract", body).then(r => r.data),
  aiPropose: (body: { target_object: string; template_id: string; sample_dataset_id?: string; client_id?: string }) =>
    api.post<{ target_object: string; ai_used: boolean; proposal_count: number; proposals: Partial<DqRule>[] }>(
      "/dq-rules/ai-propose", body).then(r => r.data),
  bulkCreateRules: (rules: Partial<DqRule>[], is_global = false) =>
    api.post<{ created: number }>("/dq-rules/bulk", { rules, is_global }).then(r => r.data),
  upload: (opts: { kind: string; target_object: string; file: File; is_global?: boolean; client_id?: string }) => {
    const fd = new FormData();
    fd.append("file", opts.file);
    return api.post<{ created: number; kind: string; target_object: string }>(
      "/dq-rules/upload", fd,
      { params: { kind: opts.kind, target_object: opts.target_object, is_global: !!opts.is_global,
                  ...(opts.client_id ? { client_id: opts.client_id } : {}) } }).then(r => r.data);
  },
  exportUrl: (params?: { target_object?: string; kind?: string }) => {
    const q = new URLSearchParams(params as any).toString();
    return `/api/dq-rules/export${q ? "?" + q : ""}`;
  },
};
