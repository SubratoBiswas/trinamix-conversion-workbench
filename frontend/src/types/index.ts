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
  /** P3 — set on the column to flag it as carrying sensitive data.
   *  Drives the 🔒 badge in Mapping Review. */
  contains_pii?: number | null;
  pii_category?: string | null;
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
  // Canonical source-system code ("netsuite" | "oracle_ebs" | "sap_ecc" | ...).
  // Pinned at project creation via the Setup Wizard; immutable once
  // conversions or connections are attached.
  source_system?: string | null;
  // Lifecycle phase ("blueprint" | "own" | "lift" | "thrive").
  phase?: string | null;
  // Fusion modules in scope on this engagement
  // (e.g. ["financials", "scm"]). Drives the Discovery panel scope,
  // Output Preview filter, and Migration Monitor entity grid.
  selected_modules?: string[] | null;
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
  source_connection_count?: number;
  has_active_source_connection?: boolean;
}

// Server-driven source-system catalog (GET /api/source-systems).
export interface SourceSystem {
  code: string;
  display_name: string;
  family: string;          // "erp" | "hcm" | "crm" | "custom"
  has_scanner_v1: boolean;
}

// Fusion module catalog (GET /api/fusion-modules). Drives the Setup
// Wizard's "Implementation Scope" step.
export interface FusionObject {
  target_object: string;
  label: string;
  fbdi_template?: string | null;
  planned_load_order: number;
  source_extracts: Record<string, string>;
}

export interface FusionModule {
  code: string;
  name: string;
  family: string;
  description: string;
  objects: FusionObject[];
}

// Per-project connection to a source ERP. Credentials live encrypted on
// the server — has_credentials is the only signal the UI ever gets back.
export interface SourceConnection {
  id: number;
  project_id: number;
  source_system: string;
  display_name: string;
  endpoint?: string | null;
  auth_type: string;
  connection_metadata?: Record<string, any>;
  has_credentials: boolean;
  mock_mode: boolean;
  status: string;          // "draft" | "ok" | "degraded" | "failed"
  last_test_at?: string | null;
  last_test_details?: {
    overall_status?: string;
    latency_ms?: number;
    version?: string;
    detected_metadata?: Record<string, any>;
    message?: string;
    probes?: { name: string; status: string; latency_ms?: number; message?: string }[];
  } | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectionTestResult {
  overall_status: string;
  latency_ms?: number | null;
  version?: string | null;
  detected_metadata?: Record<string, any>;
  probes: { name: string; status: string; latency_ms?: number | null; message?: string | null }[];
  message?: string | null;
  tested_at: string;
}

export interface AuditEvent {
  id: number;
  ts: string;
  actor_email: string;
  action: string;
  target_type?: string | null;
  target_id?: number | null;
  project_id?: number | null;
  summary?: string | null;
  details_json?: Record<string, any> | null;
  source_ip?: string | null;
  user_agent?: string | null;
}

// ── Slice 4 — Discovery ────────────────────────────────────────────

export interface DiscoveryRun {
  id: number;
  project_id: number;
  connection_id?: number | null;
  source_system: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  triggered_by?: string | null;
  total_objects: number;
  pillar_counts: Record<string, number>;
  integration_health: Record<string, number>;
  complexity_score: number;
  scan_notes?: string | null;
}

export interface DiscoveredObject {
  id: number;
  pillar: string;
  category: string;
  name: string;
  external_id?: string | null;
  risk_level?: string | null;
  last_used_at?: string | null;
  metadata_json?: Record<string, any>;
}

export interface DiscoveryLatest {
  run: DiscoveryRun | null;
  integrations: DiscoveredObject[];
}

// ── Slice 6 — Cutover & Exec layer ─────────────────────────────────

export interface Safeguard {
  code: string;
  name: string;
  status: "pass" | "warning" | "fail" | "not_run" | string;
  message: string;
  details?: Record<string, any>;
}

export interface SafeguardsResponse {
  pass_rate: number;
  safeguards: Safeguard[];
}

export interface ReadinessLens {
  label: string;
  value: number;
  value_pct: number;
  weight: number;
  details?: Record<string, any>;
}

export interface ReadinessScore {
  total: number;       // 0..5
  total_pct: number;   // 0..100
  delta_2w: number;
  lenses: Record<string, ReadinessLens>;
}

export interface ReconciliationCheck {
  id: number;
  conversion_id: number;
  metric_name: string;
  source_value: number;
  target_value: number;
  variance: number;
  variance_pct: number;
  tolerance: number;
  tolerance_pct: number;
  currency?: string | null;
  status: string;
  notes?: string | null;
  last_run_at?: string | null;
}

export interface RunbookTask {
  id: number;
  sequence: number;
  phase: string;
  title: string;
  description?: string | null;
  owner_email?: string | null;
  expected_duration_minutes: number;
  actual_duration_minutes?: number | null;
  status: string;
  severity: string;
  started_at?: string | null;
  completed_at?: string | null;
  blocker_note?: string | null;
  conversion_id?: number | null;
}

export interface Issue {
  id: number;
  project_id: number;
  conversion_id?: number | null;
  title: string;
  description?: string | null;
  owner_email?: string | null;
  raised_by?: string | null;
  severity: string;
  status: string;
  due_date?: string | null;
  resolved_at?: string | null;
  resolution_note?: string | null;
  external_ticket?: string | null;
  tags_json?: string[];
  created_at: string;
}

export interface Risk {
  id: number;
  project_id: number;
  title: string;
  description?: string | null;
  probability: number;
  impact: number;
  score: number;
  mitigation?: string | null;
  owner_email?: string | null;
  status: string;
  raised_at: string;
  closed_at?: string | null;
}

export interface DressRehearsal {
  id: number;
  project_id: number;
  sequence: number;
  scheduled_for?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_minutes?: number | null;
  result: string;
  summary?: string | null;
  findings_json?: any[];
  led_by?: string | null;
  created_at: string;
}

export interface SignOff {
  id: number;
  project_id: number;
  conversion_id?: number | null;
  kind: string;
  subject: string;
  signer_email: string;
  signer_role: string;
  decision: string;
  comment?: string | null;
  evidence_url?: string | null;
  references_signoff_id?: number | null;
  created_at: string;
}

export interface ExecSummary {
  score_pct: number;
  score_5: number;
  safeguard_pass_rate: number;
  days_to_cutover: number | null;
  open_critical_issues: number;
  top_risks: Risk[];
  top_blockers: Issue[];
  total_recon_variance_usd: number;
  pillar_complexity: number | null;
  integrations_degraded: number;
}

// ── Slice 7 — COA Engine ────────────────────────────────────────────

export interface COASegment {
  id: number;
  structure_id: number;
  position: number;
  name: string;
  length: number;
  derivation_kind: string;       // constant | source_column | crosswalk | computed | conditional
  derivation_config?: Record<string, any>;
  default_value?: string | null;
  valid_values?: string[];
  pad_style?: string;            // left_zero | right_space | none
  description?: string | null;
}

export interface COAStructure {
  id: number;
  conversion_id: number;
  name: string;
  separator: string;
  target_ledger?: string | null;
  description?: string | null;
  locked: boolean;
  segments: COASegment[];
}

export interface COACrosswalk {
  id: number;
  segment_id: number;
  legacy_value: string;
  fusion_value: string;
  description?: string | null;
  notes?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  created_by?: string | null;
}

export interface COAComposeEmission {
  segment: string;
  value: string;
  valid: boolean;
  reason?: string | null;
}

export interface COAComposedRow {
  source_index: number;
  composed_account: string;
  valid: boolean;
  emissions: COAComposeEmission[];
}

export interface COAComposeResult {
  sample_rows: COAComposedRow[];
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  coverage_pct: number;
  per_segment_coverage: Record<string, { total: number; failed: number; coverage_pct: number }>;
  per_segment_unmapped_values: Record<string, string[]>;
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
  conversion_id: number | null;
  conversion_name: string;
  target_object?: string | null;
  status: string;
  // Which conversion-workbench track the stage belongs to. The Migration
  // Monitor groups stages by track (data conversions, processes,
  // integrations) so the cutover board reflects the full workbench, not
  // just data.
  track?: "data" | "process" | "integration";
  external_id?: string | null;
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
  // P6 — dual-cert state. When `requires_dual_approval = 1`, the row
  // needs two distinct approvers before it flips to `approved`. The
  // first sign-off lands on `approved_by`; the second on
  // `second_approver_email`.
  requires_dual_approval?: number;
  second_approver_email?: string | null;
  second_approved_at?: string | null;
  // Cross-source Mapping Knowledge Bank provenance. When kb_source is set,
  // the row was pre-filled from a prior project on the same source ERP.
  // The Mapping Review UI shows a "🧠 from {Source} KB" badge and counts
  // it toward the "N pre-filled from Knowledge Bank" toast.
  kb_source?: string | null;
  kb_origin_project_id?: number | null;
  kb_times_reused?: number | null;
  sample_source_values: any[];
  sample_converted_values: any[];
}

// Per-source rollup for the Learning Center's Knowledge Bank section.
// Mirrors GET /api/learned-mappings/knowledge-bank/stats.
export interface KnowledgeBankStat {
  source_system: string;
  mappings: number;
  rules: number;
  reference_standards: number;
  project_count: number;
  total_reuses: number;
  avg_reuse_per_mapping: number;
  last_reused_at: string | null;
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
