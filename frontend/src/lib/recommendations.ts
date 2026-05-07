/**
 * Recommendation derivation engine.
 *
 * Combines a dataset's column profile + cleansing issues + (optionally)
 * a target FBDI template's field metadata into actionable recommendation
 * cards similar to Oracle Analytics Cloud's data preparation panel.
 *
 * Pure frontend logic — no extra backend roundtrip needed for the MVP.
 */
import type {
  DatasetColumnProfile,
  DatasetDetail,
  DatasetPreview,
  FBDIField,
  ValidationIssue,
} from "@/types";

export type RecommendationKind =
  | "convert_to_date"
  | "convert_to_number"
  | "remove_hyphen"
  | "remove_special_chars"
  | "trim"
  | "uppercase"
  | "value_map"
  | "default_value"
  | "deduplicate"
  | "extract_part"
  | "fix_date_format"
  | "fill_missing"
  | "standardize_uom"
  | "length_overflow";

export type RecommendationCategory =
  | "data_type"
  | "formatting"
  | "value_translation"
  | "deduplication"
  | "required"
  | "dependency_impact";

export interface Recommendation {
  id: string;
  kind: RecommendationKind;
  category: RecommendationCategory;
  title: string;
  reason: string;
  impact: { records: number; details?: string };
  confidence: number;     // 0..1
  column: string;
  targetField?: string;   // when tied to an FBDI target field
  preview?: { before: string; after: string }[];
  ruleType?: string;      // matches backend transformation engine rule_type
  ruleConfig?: any;
}

const ID = (() => {
  let c = 0;
  return () => `rec_${Date.now().toString(36)}_${(++c).toString(36)}`;
})();

const DATE_RE = /^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$/;
const NUM_RE = /^[+-]?\d+([.,]\d+)?$/;

// ---------- helpers ----------

const sampleValues = (col: DatasetColumnProfile): string[] =>
  (col.sample_values || []).map((v) => (v == null ? "" : String(v))).filter(Boolean);

const looksLikeDate = (vals: string[]) =>
  vals.length > 0 && vals.every((v) => DATE_RE.test(v.trim()));

const looksLikeNumber = (vals: string[]) =>
  vals.length > 0 && vals.every((v) => NUM_RE.test(v.trim()));

const containsHyphen = (vals: string[]) => vals.some((v) => /-/.test(v));

const hasLeadingTrailingWS = (vals: string[]) =>
  vals.some((v) => v !== v.trim());

const looksMixedCase = (vals: string[]) => {
  const upper = vals.filter((v) => v && v === v.toUpperCase()).length;
  const lower = vals.filter((v) => v && v === v.toLowerCase()).length;
  return upper > 0 && lower > 0;
};

// Common categorical translations Oracle expects
const COMMON_VALUE_MAPS: Record<string, Record<string, string>> = {
  status: { A: "Active", I: "Inactive", X: "Inactive", "0": "Inactive", "1": "Active" },
  flag: { Y: "Yes", N: "No", "1": "Yes", "0": "No" },
};

// ---------- main API ----------

export interface BuildRecommendationsInput {
  dataset: DatasetDetail;
  preview?: DatasetPreview | null;
  cleansing?: ValidationIssue[];
  targetFields?: FBDIField[];   // optional — when project is bound to a template
}

export function buildRecommendations(input: BuildRecommendationsInput): Recommendation[] {
  const { dataset, preview, cleansing = [], targetFields = [] } = input;
  const recs: Recommendation[] = [];

  // Column-level recommendations driven by the profile + sample values
  for (const col of dataset.columns) {
    const samples = sampleValues(col);
    const total = dataset.row_count;
    const nullCount = col.null_count;

    // 1. Date detection — column inferred as string but values look like dates
    if (col.inferred_type === "string" && looksLikeDate(samples)) {
      recs.push({
        id: ID(),
        kind: "convert_to_date",
        category: "data_type",
        title: `Convert ${col.column_name} to Date`,
        reason: `Detected ${samples[0]?.includes("/") ? "MM/DD/YYYY" : "YYYY-MM-DD"} pattern in samples — Fusion expects ISO date format.`,
        impact: { records: total - nullCount },
        confidence: 0.92,
        column: col.column_name,
        ruleType: "DATE_FORMAT",
        ruleConfig: { input_format: "%m/%d/%Y", output_format: "%Y/%m/%d" },
        preview: samples.slice(0, 2).map((v) => ({
          before: v, after: oraclize(v),
        })),
      });
    }

    // 2. Number detection — string column with numeric values
    if (col.inferred_type === "string" && looksLikeNumber(samples) && samples.length >= 3) {
      recs.push({
        id: ID(),
        kind: "convert_to_number",
        category: "data_type",
        title: `Convert ${col.column_name} to Number`,
        reason: "Sample values are numeric. FBDI target requires Number for amounts and quantities.",
        impact: { records: total - nullCount },
        confidence: 0.88,
        column: col.column_name,
        ruleType: "NUMBER_FORMAT",
        ruleConfig: { decimals: 2 },
      });
    }

    // 3. Hyphen normalization — common in part numbers / SKU codes
    if (col.inferred_type === "string" && containsHyphen(samples)) {
      const looksLikeSku = /num|sku|code|id|item|part/i.test(col.column_name);
      if (looksLikeSku) {
        recs.push({
          id: ID(),
          kind: "remove_hyphen",
          category: "formatting",
          title: `Remove hyphen from ${col.column_name}`,
          reason: "FBDI item-number columns typically reject hyphenated formats. Normalising will match canonical SKU values.",
          impact: { records: samples.filter((v) => /-/.test(v)).length * 5 },
          confidence: 0.86,
          column: col.column_name,
          ruleType: "REMOVE_HYPHEN",
          ruleConfig: {},
          preview: samples.filter((v) => /-/.test(v)).slice(0, 2).map((v) => ({
            before: v, after: v.replace(/-/g, ""),
          })),
        });
      }
    }

    // 4. Whitespace trim
    if (hasLeadingTrailingWS(samples)) {
      recs.push({
        id: ID(),
        kind: "trim",
        category: "formatting",
        title: `Trim whitespace in ${col.column_name}`,
        reason: "Leading or trailing spaces detected — these break exact-match lookups in Fusion.",
        impact: { records: samples.filter((v) => v !== v.trim()).length },
        confidence: 0.95,
        column: col.column_name,
        ruleType: "TRIM",
        ruleConfig: {},
      });
    }

    // 5. Casing normalization
    if (col.inferred_type === "string" && looksMixedCase(samples) &&
        /uom|status|code|currency|country|flag/i.test(col.column_name)) {
      recs.push({
        id: ID(),
        kind: "uppercase",
        category: "formatting",
        title: `Uppercase ${col.column_name} values`,
        reason: "Inconsistent casing detected. Fusion lookups for codes are case-sensitive.",
        impact: { records: samples.filter((v) => v && v !== v.toUpperCase()).length },
        confidence: 0.78,
        column: col.column_name,
        ruleType: "UPPERCASE",
        ruleConfig: {},
      });
    }

    // 6. Value map — short categorical column with A/I/Y/N pattern
    if (col.inferred_type === "string" && col.distinct_count <= 4 && samples.length >= 2) {
      const upperCol = col.column_name.toLowerCase();
      const map = upperCol.includes("status") ? COMMON_VALUE_MAPS.status :
                  upperCol.includes("flag") ? COMMON_VALUE_MAPS.flag : null;
      if (map) {
        const matched = samples.filter((v) => v.toUpperCase() in map);
        if (matched.length > 0) {
          recs.push({
            id: ID(),
            kind: "value_map",
            category: "value_translation",
            title: `Map ${col.column_name} values`,
            reason: `Legacy values (${matched.slice(0, 3).join(", ")}) detected. Fusion expects descriptive labels.`,
            impact: { records: total - nullCount },
            confidence: 0.84,
            column: col.column_name,
            ruleType: "VALUE_MAP",
            ruleConfig: map,
            preview: matched.slice(0, 2).map((v) => ({
              before: v, after: map[v.toUpperCase()],
            })),
          });
        }
      }
    }

    // 7. Missing values — fill with default
    if (nullCount > 0 && nullCount / Math.max(total, 1) >= 0.05) {
      recs.push({
        id: ID(),
        kind: "fill_missing",
        category: "required",
        title: `Fill missing values in ${col.column_name}`,
        reason: `${col.null_percent}% of rows have no value. Provide a default to avoid load failures on required fields.`,
        impact: { records: nullCount },
        confidence: 0.6,
        column: col.column_name,
        ruleType: "DEFAULT_VALUE",
        ruleConfig: { value: "" },
      });
    }

    // 8. Length overflow — sample value longer than nearest target field's max_length
    if (targetFields.length > 0 && samples.length > 0) {
      const maxLen = Math.max(...samples.map((v) => v.length));
      const matchingTarget = guessTargetForColumn(col, targetFields);
      if (matchingTarget?.max_length && maxLen > matchingTarget.max_length) {
        recs.push({
          id: ID(),
          kind: "length_overflow",
          category: "data_type",
          title: `${col.column_name} values exceed target max length`,
          reason: `Some values are ${maxLen} chars; target ${matchingTarget.field_name} caps at ${matchingTarget.max_length}.`,
          impact: { records: samples.filter((v) => v.length > matchingTarget.max_length!).length * 3 },
          confidence: 0.7,
          column: col.column_name,
          targetField: matchingTarget.field_name,
        });
      }
    }
  }

  // Dataset-wide recommendations
  // Deduplication — if any column looks like an identifier and preview shows duplicates
  if (preview) {
    const idCol = dataset.columns.find((c) =>
      /num|id|sku|code/i.test(c.column_name) && c.distinct_count > 0
    );
    if (idCol && idCol.distinct_count < dataset.row_count) {
      const dupCount = dataset.row_count - idCol.distinct_count;
      recs.push({
        id: ID(),
        kind: "deduplicate",
        category: "deduplication",
        title: `Deduplicate on ${idCol.column_name}`,
        reason: `${dupCount} duplicate ${idCol.column_name} value(s) detected. Fusion master-data loads will reject duplicates.`,
        impact: { records: dupCount, details: `${dupCount} duplicate group(s)` },
        confidence: 0.93,
        column: idCol.column_name,
      });
    }
  }

  // Surface cleansing-engine issues as recommendations
  for (const issue of cleansing) {
    if (issue.severity === "info") continue;
    if (issue.issue_type.toLowerCase().includes("unmapped required") && issue.field_name) {
      recs.push({
        id: ID(),
        kind: "fill_missing",
        category: "required",
        title: `Required field unmapped: ${issue.field_name}`,
        reason: issue.suggested_fix || issue.message,
        impact: { records: issue.impacted_count || 0 },
        confidence: 0.95,
        column: issue.field_name,
      });
    }
  }

  // Sort by confidence × impact
  return recs.sort((a, b) =>
    (b.confidence * (1 + Math.log10(Math.max(b.impact.records, 1)))) -
    (a.confidence * (1 + Math.log10(Math.max(a.impact.records, 1))))
  );
}

// Try to guess which FBDI target field a source column might map to (cheap heuristic)
function guessTargetForColumn(
  col: DatasetColumnProfile,
  targets: FBDIField[],
): FBDIField | undefined {
  const tokens = col.column_name.toLowerCase().replace(/[_-]/g, " ").split(/\s+/);
  let best: { field: FBDIField; score: number } | null = null;
  for (const t of targets) {
    const tt = (t.field_name || "").toLowerCase().replace(/[_-]/g, " ").split(/\s+/);
    const overlap = tokens.filter((x) => tt.includes(x)).length;
    if (overlap > 0) {
      const score = overlap / Math.max(tokens.length, tt.length);
      if (!best || score > best.score) best = { field: t, score };
    }
  }
  return best?.field;
}

// Render a "Oracle date" preview from MM/DD/YYYY → YYYY/MM/DD
function oraclize(v: string): string {
  const m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
  if (!m) return v;
  const [, mm, dd, yy] = m;
  const yyyy = yy.length === 2 ? `20${yy}` : yy;
  return `${yyyy}/${mm.padStart(2, "0")}/${dd.padStart(2, "0")}`;
}

// Group / categorise for the right-side filter chips
export const CATEGORY_LABELS: Record<RecommendationCategory, string> = {
  data_type: "Data Type",
  formatting: "Formatting",
  value_translation: "Value Translation",
  deduplication: "Deduplication",
  required: "Required Fields",
  dependency_impact: "Dependency Impact",
};
