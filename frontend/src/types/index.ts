// Shared types — keep aligned with backend Pydantic schemas.

export interface User { id: number; name: string; email: string; role: string; }

export interface Dataset {
  id: number;
  name: string;
  description?: string | null;
  file_name: string;
  file_type: string;
  row_count: number;
  column_count: number;
  status: string;
  uploaded_at: string;
}

export interface DatasetColumnProfile {
  id: number;
  column_name: string;
  position: number;
  inferred_type: string | null;
  null_count: number;
  null_percent: number;
  distinct_count: number;
  sample_values: any[];
  min_value: string | null;
  max_value: string | null;
  pattern_summary: string | null;
}

export interface DatasetDetail extends Dataset {
  columns: DatasetColumnProfile[];
}

export interface DatasetPreview {
  columns: string[];
  rows: Record<string, any>[];
  total_rows: number;
}

export interface FBDISheet {
  id: number;
  template_id: number;
  sheet_name: string;
  sequence: number;
  field_count: number;
}

export interface FBDIField {
  id: number;
  template_id: number;
  sheet_id: number;
  field_name: string;
  display_name: string | null;
  description: string | null;
  required: boolean;
  data_type: string | null;
  max_length: number | null;
  format_mask: string | null;
  sample_value: string | null;
  lookup_type: string | null;
  validation_notes: string | null;
  sequence: number;
  required_modules: string[];
}

export interface FBDITemplate {
  id: number;
  name: string;
  module: string | null;
  tier: string;            // T0 | T1 | T2 | T3
  phase: string;           // Blueprint | Build | Validation | Cutover
  business_object: string | null;
  required_field_count: number;
  version: string;
  file_name: string | null;
  status: string;
  description: string | null;
  uploaded_at: string;
}

export interface FBDITemplateDetail extends FBDITemplate {
  sheets: FBDISheet[];
  field_count: number;
}

// Engagement-level project (e.g. "Trinamix → Oracle SCM Cloud Phase 1").
// Contains many Conversion objects.
export interface Project {
  id: number;
  name: string;
  description?: string | null;
  client?: string | null;
  target_environment?: string | null;
  go_live_date?: string | null;
  owner?: string | null;
  status: string;
  production_cutover_start?: string | null;
  production_cutover_end?: string | null;
  migration_lead?: string | null;
  data_owner?: string | null;
  sox_controlled?: number | null;
  created_at: string;
  updated_at: string;
  // Roll-ups
  conversion_count?: number;
  in_progress_count?: number;
  loaded_count?: number;
  failed_count?: number;
}

export interface Environment {
  id: number;
  project_id: number;
  name: string;
  description?: string | null;
  sort_order: number;
  color: string;
  sox_controlled: number;
  created_at: string;
}

export interface EnvironmentRun {
  id: number;
  environment_id: number;
  conversion_id: number;
  dataset_id?: number | null;
  status: string;
  stage?: string | null;
  record_count?: number | null;
  passed_count?: number | null;
  failed_count?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  notes?: string | null;
  environment_name?: string | null;
  conversion_name?: string | null;
  dataset_name?: string | null;
}

export interface CutoverStage {
  conversion_id: number;
  conversion_name: string;
  target_object?: string | null;
  status: string;
  run_id?: number | null;
  dataset_id?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface CutoverEnvironmentColumn {
  id: number;
  name: string;
  color: string;
  sox_controlled: boolean;
  stages: CutoverStage[];
  complete_count: number;
  running_count: number;
  failed_count: number;
  pending_count: number;
}

export interface CutoverDashboard {
  project_id: number;
  project_name: string;
  days_to_go_live?: number | null;
  cutover_window_start?: string | null;
  cutover_window_end?: string | null;
  sox_controlled: boolean;
  environments: CutoverEnvironmentColumn[];
  pipeline_runs: {
    run_id: number;
    entity: string;
    stage?: string | null;
    status: string;
    records?: number | null;
    started?: string | null;
    environment?: string | null;
  }[];
}

// One conversion object inside an engagement (e.g. "Item Master Conversion").
export interface Conversion {
  id: number;
  project_id: number;
  name: string;
  description?: string | null;
  target_object?: string | null;
  dataset_id?: number | null;
  template_id?: number | null;
  planned_load_order: number;
  status: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  dataset_name?: string | null;
  template_name?: string | null;
  project_name?: string | null;
}

/** @deprecated kept temporarily so unmigrated pages still compile.
 * Will be removed once every page is on the new model. */
export type ConversionProject = Conversion;

export interface MappingSuggestion {
  id: number;
  conversion_id: number;
  target_field_id: number;
  target_field_name: string | null;
  target_required: boolean;
  target_data_type: string | null;
  target_max_length: number | null;
  source_column: string | null;
  confidence: number;
  reason: string | null;
  suggested_transformation: { rule_type: string; config: any; description?: string } | null;
  review_required: number;
  status: string;
  default_value: string | null;
  comment: string | null;
  approved_by: string | null;
  approved_at: string | null;
  sample_source_values: any[];
  sample_converted_values: any[];
}

export interface TransformationRule {
  id: number;
  conversion_id: number;
  target_field_id: number | null;
  source_column: string | null;
  rule_type: string;
  rule_config: Record<string, any>;
  description: string | null;
  sequence: number;
  created_at: string;
}

export interface ValidationIssue {
  id: number;
  conversion_id: number;
  category: "cleansing" | "validation";
  row_number: number | null;
  field_name: string | null;
  issue_type: string;
  severity: "info" | "warning" | "error" | "critical";
  message: string;
  suggested_fix: string | null;
  auto_fixable: boolean;
  impacted_count: number;
  status: string;
  created_at: string;
}

export interface ConvertedOutput {
  id: number;
  conversion_id: number;
  output_file_name: string;
  row_count: number;
  column_count: number;
  status: string;
  generated_at: string;
}

export interface OutputPreview {
  columns: string[];
  rows: Record<string, any>[];
  total_rows: number;
  lineage: Record<string, { source_column: string | null; default_value?: string | null; rules: any[]; status: string; confidence: number }>;
}

export interface LoadRun {
  id: number;
  conversion_id: number;
  run_type: string;
  status: string;
  total_records: number;
  passed_count: number;
  failed_count: number;
  warning_count: number;
  error_count: number;
  started_at: string;
  completed_at: string | null;
}

export interface LoadError {
  id: number;
  row_number: number | null;
  object_name: string | null;
  error_category: string | null;
  error_message: string | null;
  root_cause: string | null;
  related_dependency: string | null;
  reference_value: string | null;
  suggested_fix: string | null;
}

export interface LoadSummary {
  total_records: number;
  passed_count: number;
  failed_count: number;
  warning_count: number;
  error_count: number;
  error_categories: { name: string; count: number }[];
  root_causes: { cause: string; count: number }[];
  dependency_impacts: { object: string; count: number }[];
}

export interface Workflow {
  id: number;
  name: string;
  description: string | null;
  conversion_id: number | null;
  nodes: any[];
  edges: any[];
  status: string;
  last_run_at: string | null;
  last_run_summary: any | null;
  created_at: string;
  updated_at: string;
}

export interface Dependency {
  id: number;
  source_object: string;
  target_object: string;
  relationship_type: string;
  description: string | null;
}

export interface DashboardKpis {
  total_datasets: number;
  total_templates: number;
  total_projects: number;
  total_conversions: number;
  total_workflows: number;
  total_load_runs: number;
  pass_rate: number;
  fail_rate: number;
  recent_projects: any[];
  recent_conversions: any[];
  recent_load_runs: any[];
  project_status_breakdown: { status: string; count: number }[];
  conversion_status_breakdown: { status: string; count: number }[];
  load_status_breakdown: { status: string; count: number }[];
}

export interface LearnedMapping {
  id: number;
  kind: string;
  category: string;
  original_value: string;
  resolved_value: string;
  target_object?: string | null;
  target_field?: string | null;
  rule_type?: string | null;
  rule_config?: any;
  project_id?: number | null;
  captured_from?: string | null;
  captured_by?: string | null;
  captured_at: string;
  confidence_boost: number;
  records_auto_fixed: number;
}

export interface LearningStats {
  total: number;
  avg_confidence_boost: number;
  records_auto_fixed: number;
  analyst_minutes_saved: number;
  by_category: { category: string; count: number }[];
}
