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
  conversion_count?: number;
  conversion_names?: string[];
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
  sheet_name?: string | null;
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
  client_id?: string | null;
  client_name?: string | null;
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
  planning_count?: number;
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
  /** Representative row counts per source ERP for mock mode display.
   *  Key = source ERP code ("oracle_ebs", "netsuite", ...). */
  mock_row_counts?: Record<string, number>;
}

/** Response from GET /api/projects/{id}/discovery/scope-hints */
export interface ScopeHints {
  is_mock: boolean;
  run_id?: string | null;
  /** Map of UPPER_CASE_TABLE_NAME → row_count (null = table found but count unavailable) */
  table_counts: Record<string, number | null>;
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
  id: string;
  project_id?: string | null;
  connection_id?: string | null;
  source_system?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  triggered_by?: string | null;
  total_objects: number;
  pillar_counts: Record<string, number>;
  integration_health: Record<string, number>;
  complexity_score: number;
  scan_notes?: string | null;
  is_mock?: boolean;
}

export interface DiscoveredObject {
  id: string;
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
  /** "dataset" = uploaded file source; "ebs" = live Oracle EBS query */
  source_type?: string;
  /** EBS staging table for this conversion, e.g. "MTL_SYSTEM_ITEMS_B" */
  ebs_table_hint?: string | null;
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

/** One ranked alternative source-column candidate for a target field. */
export interface MappingCandidate {
  source_column: string;
  confidence: number;
  raw_confidence?: number;
  plausible?: boolean;
  source_category?: string;
  target_category?: string;
  caution?: string | null;
  inferred_type: string | null;
  null_percent: number;
  sample_values: string[];
  reasons: string[];
  /** AI verdict, filled by the on-demand vetting pass. */
  ai_verdict?: string;
  ai_reason?: string;
}

/** Alternative candidates for a single target field. */
export interface MappingCandidateGroup {
  target_field_id: string;
  target_field_name: string | null;
  candidates: MappingCandidate[];
}

export interface MappingSuggestion {
  id: number;
  conversion_id: number;
  target_field_id: number;
  target_field_name: string | null;
  target_required: boolean;
  target_data_type: string | null;
  target_max_length: number | null;
  /** Destination list of values: [{code, meaning}]. Non-empty means the
   *  field is LOV-constrained and value-map recommendations are available. */
  target_lov?: { code: string; meaning?: string }[];
  target_default_if_blank?: string | null;
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

// ─── Duplicate / cleansing review (row-level decisions) ───
/** Verdicts the backend accepts for a duplicate cluster (RowDecision.DUP_VERDICTS). */
export type DuplicateVerdict =
  | "keep_survivor" | "merge" | "keep_all" | "exclude"
  /** Keep a named subset — for a cluster that is only PARTLY duplicated. */
  | "keep_subset";
/** Verdicts the backend accepts for a cleansing finding (RowDecision.CLEANSE_VERDICTS). */
export type CleansingVerdict = "apply" | "ignore";

export interface DuplicateMember {
  row: number;
  /** Stable identity hash — null when the row could not be keyed. */
  key: string | null;
  values: Record<string, string>;
}

export interface DuplicateDecision {
  verdict: DuplicateVerdict;
  survivor_key: string | null;
  /** keep_subset only: the member keys to keep. Everything else drops. */
  keep_keys?: string[];
  /** "learned" = carried over from an earlier conversion for this client + object. */
  source: "conversion" | "learned";
}

export interface DuplicateCluster {
  confidence: number;
  size: number;
  fields: string[];
  /** AI adjudication (only present when the scan ran with use_ai=true). */
  verdict?: "same" | "different" | "unsure";
  ai_reason?: string;
  cluster_key: string;
  member_keys: string[];
  /** Strong identifiers (tax id, DUNS) whose values DISAGREE across the cluster.
   *  Advisory: two different tax registrations usually mean two legal entities,
   *  so merging would pick one arbitrarily. The cluster is never auto-split. */
  id_conflicts?: { column: string; values: string[] }[];
  decision: DuplicateDecision | null;
  members: DuplicateMember[];
}

export interface DuplicateCandidates {
  object?: string;
  rows_scanned: number;
  /** Plain column names in the happy path, may be absent on early-return branches. */
  identity_fields: string[];
  /** Always plain column names — prefer this for rendering member values. */
  identity_columns?: string[];
  anchor?: string;
  cluster_count?: number;
  duplicate_rows?: number;
  truncated?: boolean;
  /** How many clusters were actually returned (at most `max_clusters`). */
  returned_count?: number;
  /** cluster_count - returned_count: groups the scan found but did NOT send, so
   *  they cannot be reviewed or decided here. decided_count / undecided_count
   *  describe the RETURNED groups only. */
  hidden_count?: number;
  max_clusters?: number;
  note?: string;
  /** How much of the data the scan actually examined, and by which method.
   *  A name group too large to compare pairwise falls back to comparing each row
   *  against its nearest neighbours by name — much cheaper, but a distant pair
   *  inside that group can be missed. Rows with no value in the anchor column
   *  cannot be name-matched at all. `coverage_note` states both in plain English and
   *  is EMPTY when coverage was complete. Before this, "no duplicates found" and
   *  "those rows were never fully compared" produced identical output. */
  rows_compared?: number;
  rows_windowed?: number;
  windowed_blocks?: number;
  rows_without_anchor?: number;
  coverage_note?: string;
  ai_used?: boolean;
  sources?: string[];
  decided_count?: number;
  undecided_count?: number;
  clusters: DuplicateCluster[];
}

export interface RequiredCheck {
  conversion_id?: string;
  target_object?: string;
  required_total: number;
  failed_count: number;
  partial_count: number;
  /** True when a required field is absent or wholly empty — Oracle would reject
   *  every row, so generation is refused rather than warned about. */
  blocked: boolean;
  message?: string;
  failures: { sheet: string; field: string }[];
  partials: { sheet: string; field: string }[];
  sheets?: {
    sheet: string; sheet_generated: boolean; rows: number;
    failed: string[]; partial: string[];
    checks: { field: string; status: string; column?: string | null;
              rows: number; present: number; blank: number }[];
  }[];
}

export interface MappingReport {
  conversion_id?: string;
  target_object?: string | null;
  generated_at?: string | null;
  headline: string;
  blocked: boolean;
  output_stale?: boolean;
  mapping: {
    total_fields: number;
    mapped: number;
    /** Fields resolved by the matcher or AI alone — nothing human or signed
     *  stands behind them. A disclosure for sign-off, not a quality score. */
    unattested: number;
    by_layer: { layer: string; label: string; count: number }[];
    required_unmapped: string[];
  };
  validation: {
    checked: number; failed: number; warnings: number; passed: number;
    hard_error_count?: number; by_type: Record<string, number>;
  };
  cleansing: {
    rules_fired: number; values_changed: number; fields_touched: number;
    by_rule: { rule: string; count: number }[];
  };
  rules: { configured: number; applied: number };
  required_fields: {
    checked: number; failed: number; partial: number; passed: number;
    failures: { sheet: string; field: string }[];
    partials: { sheet: string; field: string }[];
  };
}

/** Cleansing rule families — mirrors services/cleansing_rules.FAMILIES. */
export type CleansingFamily =
  | "whitespace_punct" | "special_chars" | "case" | "legal_suffix";

export interface CleansingProfile {
  families: CleansingFamily[];
  ascii_fold?: boolean;
  /** Per-column override; a column listed here ignores `families`. */
  per_field?: Record<string, CleansingFamily[]>;
  exclude_fields?: string[];
  /** Analyst corrections: {field: {original value: replacement}}. An override
   *  beats every rule, so a reviewer can fix one bad result without disabling a
   *  family that is right about the other thousands of values. */
  value_overrides?: Record<string, Record<string, string>>;
}

export interface CleansingProfileInfo {
  conversion_id: string;
  profile: CleansingProfile;
  /** True while the conversion has never been configured (safe defaults apply). */
  is_default: boolean;
  families: {
    key: CleansingFamily;
    label: string;
    /** Safe families only remove meaningless characters. The others REWRITE
     *  business values (casing, legal suffixes), so they default to off. */
    safe: boolean;
    enabled: boolean;
  }[];
}

export interface CleansingPreview {
  conversion_id?: string;
  families: CleansingFamily[];
  total_changes: number;
  fields_affected: number;
  rows_scanned?: number;
  findings: {
    field: string;
    /** A family key, or "override" for a change the analyst pinned by hand. */
    rule: CleansingFamily | "override";
    label?: string;
    count: number;
    examples: { before: string; after: string }[];
  }[];
}

export interface CleansingFinding {
  key: string;
  category: string | null;
  field_name: string | null;
  issue_type: string | null;
  severity: string;
  message: string;
  suggested_fix: string | null;
  auto_fixable: boolean;
  impacted_count: number;
  verdict: CleansingVerdict | null;
}

export interface ReviewBundle {
  conversion_id: string;
  target_object: string | null;
  cleansing: CleansingFinding[];
  cleansing_open: number;
  duplicate_decisions: number;
}

export interface DecisionInput {
  scope: "duplicate" | "cleansing";
  decision_key: string;
  verdict: DuplicateVerdict | CleansingVerdict;
  survivor_key?: string | null;
  member_keys?: string[];
  /** keep_subset only. */
  keep_keys?: string[];
  label?: string;
  note?: string;
  /** Reuse this verdict for the same client + object next time (defaults true for keep_all). */
  promote?: boolean;
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
  /** Mongo ObjectId hex string. Was typed `number`, a leftover from the SQL era;
   *  every call site already passes it to string-typed APIs, which is why the two
   *  `Argument of type 'number'` errors in LearningCenterPage existed. */
  id: string;
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
  /** Which legacy system this learning came from. Item mappings from NetSuite and
   *  from SyteLine are different mappings for the same target field, and the engine
   *  already scopes lookups by this — it was simply absent from the payload, so the
   *  two were indistinguishable on screen. */
  source_erp?: string | null;
  /** Per-interface-sheet scope. Empty/empty means every sheet. `exclude_sheets`
   *  wins over `sheets`. Oracle repeats field names across sheets (Customer has 19),
   *  so these are what stop one approval reaching all of them. Both were write-only
   *  until the response schema declared them. */
  sheets?: string[];
  exclude_sheets?: string[];
}

export interface LearningStats {
  total: number;
  objects_covered: number;
  reusable_no_ai: number;
  times_applied: number;
  by_category: { category: string; count: number }[];
  by_source?: { source: string; count: number }[];
}
