/**
 * Centralised registry for every node type the Dataflow Builder supports.
 *
 * Each entry defines the palette appearance, canvas styling, default config,
 * and the schema of the properties form rendered in the bottom panel. This is
 * the single source of truth — palette, canvas, and properties panel all read
 * from here so adding a new node type is one edit.
 */
import {
  Database, FileSpreadsheet, Sparkles, Filter, Combine, Layers,
  Columns3, Wand2, ListChecks, CloudUpload, GitMerge, Save,
  Type, GitBranch, FileOutput, Eye, Sigma, Edit3, SplitSquareHorizontal,
  Copy, Workflow as WfIcon,
} from "lucide-react";

export type NodeCategory =
  | "data_sources"
  | "conversion"
  | "transformations"
  | "quality"
  | "output";

export interface NodeFieldSchema {
  key: string;
  label: string;
  kind: "text" | "number" | "select" | "multiselect" | "textarea" | "json" | "datasetPicker" | "templatePicker" | "columnsPicker";
  options?: { value: string; label: string }[];
  helper?: string;
  required?: boolean;
  placeholder?: string;
  default?: any;
}

export interface NodeTypeDef {
  type: string;
  label: string;
  category: NodeCategory;
  icon: React.ElementType;
  /** Tailwind background utility for the node body */
  bg: string;
  /** Tailwind text/border utility for the node icon + accent */
  accent: string;
  /** Description shown as tooltip + in properties panel */
  description: string;
  /** Form fields rendered in the bottom panel when this node is selected */
  fields?: NodeFieldSchema[];
  /** Whether the workflow runner backend supports this node */
  runnable?: boolean;
  /** Default data to seed when a node of this type is created */
  defaultData?: Record<string, any>;
}

export const NODE_CATEGORIES: { key: NodeCategory; label: string }[] = [
  { key: "data_sources",     label: "Data Sources" },
  { key: "conversion",       label: "Conversion" },
  { key: "transformations",  label: "Transformations" },
  { key: "quality",          label: "Quality" },
  { key: "output",           label: "Output" },
];

export const NODE_TYPES: Record<string, NodeTypeDef> = {
  // ─────── DATA SOURCES ───────
  dataset: {
    type: "dataset",
    label: "Add Data",
    category: "data_sources",
    icon: Database,
    bg: "bg-emerald-50",
    accent: "text-emerald-600 border-emerald-300",
    description: "Source dataset from the catalogue.",
    fields: [
      { key: "datasetId", label: "Dataset", kind: "datasetPicker", required: true,
        helper: "Pick a profiled dataset from the catalogue." },
    ],
    defaultData: { label: "Add Data" },
  },
  save_dataset: {
    type: "save_dataset",
    label: "Save Dataset",
    category: "data_sources",
    icon: Save,
    bg: "bg-emerald-50",
    accent: "text-emerald-600 border-emerald-300",
    description: "Persist the current step as a reusable dataset.",
    fields: [
      { key: "name", label: "Saved name", kind: "text", placeholder: "e.g. Items_cleansed" },
    ],
    defaultData: { label: "Save Dataset" },
  },

  // ─────── CONVERSION ───────
  fbdi_template: {
    type: "fbdi_template",
    label: "Select Target FBDI",
    category: "conversion",
    icon: FileSpreadsheet,
    bg: "bg-indigo-50",
    accent: "text-indigo-600 border-indigo-300",
    description: "Bind an Oracle FBDI template as the conversion target.",
    fields: [
      { key: "templateId", label: "FBDI template", kind: "templatePicker", required: true,
        helper: "Required-field metadata flows downstream from this node." },
    ],
    defaultData: { label: "Select Target FBDI" },
  },
  ai_auto_map: {
    type: "ai_auto_map",
    label: "AI Auto Map",
    category: "conversion",
    icon: Sparkles,
    bg: "bg-indigo-50",
    accent: "text-indigo-600 border-indigo-300",
    description: "Run the AI mapping engine to suggest source → target mappings.",
    fields: [
      { key: "minConfidence", label: "Min confidence threshold", kind: "number", default: 0.2,
        helper: "Suggestions below this are dropped." },
      { key: "preserveApproved", label: "Preserve manual approvals", kind: "select",
        options: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }], default: "yes",
        helper: "Re-running mapping won't overwrite human-approved decisions." },
    ],
    runnable: true,
    defaultData: { label: "AI Auto Map" },
  },

  // ─────── TRANSFORMATIONS ───────
  join: {
    type: "join",
    label: "Join",
    category: "transformations",
    icon: GitMerge,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Join two upstream datasets on a common key.",
    fields: [
      { key: "leftKey",  label: "Left key",  kind: "text", required: true, placeholder: "e.g. legacy_item_num" },
      { key: "rightKey", label: "Right key", kind: "text", required: true, placeholder: "e.g. item_id" },
      { key: "joinType", label: "Join type", kind: "select",
        options: [
          { value: "inner", label: "Inner" }, { value: "left", label: "Left" },
          { value: "right", label: "Right" }, { value: "full", label: "Full outer" },
        ], default: "inner" },
    ],
    defaultData: { label: "Join" },
  },
  union: {
    type: "union",
    label: "Union Rows",
    category: "transformations",
    icon: Combine,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Stack two datasets vertically (rows).",
    defaultData: { label: "Union Rows" },
  },
  filter: {
    type: "filter",
    label: "Filter",
    category: "transformations",
    icon: Filter,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Drop rows that don't match the predicate.",
    fields: [
      { key: "expression", label: "Filter expression", kind: "textarea",
        placeholder: "status = 'A' AND unit_cost > 0",
        helper: "SQL-like expression evaluated per row." },
    ],
    defaultData: { label: "Filter" },
  },
  aggregate: {
    type: "aggregate",
    label: "Aggregate",
    category: "transformations",
    icon: Sigma,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Group by columns and compute aggregations.",
    fields: [
      { key: "groupBy", label: "Group by columns", kind: "multiselect", default: [] },
      { key: "agg",     label: "Aggregation",      kind: "select",
        options: [
          { value: "count", label: "Count" }, { value: "sum", label: "Sum" },
          { value: "avg", label: "Average" }, { value: "min", label: "Min" }, { value: "max", label: "Max" },
        ], default: "count" },
    ],
    defaultData: { label: "Aggregate" },
  },
  select_columns: {
    type: "select_columns",
    label: "Select Columns",
    category: "transformations",
    icon: Columns3,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Project a subset of columns forward.",
    fields: [
      { key: "columns", label: "Columns to keep", kind: "columnsPicker", default: [] },
    ],
    defaultData: { label: "Select Columns" },
  },
  rename_columns: {
    type: "rename_columns",
    label: "Rename Columns",
    category: "transformations",
    icon: Edit3,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Rename columns to canonical names.",
    fields: [
      { key: "renames", label: "Renames (JSON map)", kind: "json", default: {},
        helper: 'e.g. {"legacy_item_num": "ItemNumber"}' },
    ],
    defaultData: { label: "Rename Columns" },
  },
  transform: {
    type: "transform",
    label: "Transform Column",
    category: "transformations",
    icon: Wand2,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Apply a transformation rule to a column.",
    fields: [
      { key: "column",    label: "Source column", kind: "text", required: true },
      { key: "ruleType",  label: "Rule type",     kind: "select",
        options: [
          { value: "TRIM",                 label: "TRIM" },
          { value: "UPPERCASE",            label: "UPPERCASE" },
          { value: "LOWERCASE",            label: "LOWERCASE" },
          { value: "REMOVE_HYPHEN",        label: "REMOVE_HYPHEN" },
          { value: "REMOVE_SPECIAL_CHARS", label: "REMOVE_SPECIAL_CHARS" },
          { value: "REPLACE",              label: "REPLACE" },
          { value: "DEFAULT_VALUE",        label: "DEFAULT_VALUE" },
          { value: "VALUE_MAP",            label: "VALUE_MAP" },
          { value: "DATE_FORMAT",          label: "DATE_FORMAT" },
          { value: "NUMBER_FORMAT",        label: "NUMBER_FORMAT" },
        ], default: "TRIM" },
      { key: "ruleConfig", label: "Rule config (JSON)", kind: "json", default: {} },
    ],
    defaultData: { label: "Transform Column" },
  },
  merge_columns: {
    type: "merge_columns",
    label: "Merge Columns",
    category: "transformations",
    icon: Combine,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Concatenate multiple columns into one.",
    fields: [
      { key: "sources",   label: "Source columns", kind: "multiselect", default: [] },
      { key: "separator", label: "Separator",      kind: "text", default: " " },
      { key: "newColumn", label: "New column",     kind: "text", placeholder: "e.g. full_name" },
    ],
    defaultData: { label: "Merge Columns" },
  },
  split_columns: {
    type: "split_columns",
    label: "Split Columns",
    category: "transformations",
    icon: SplitSquareHorizontal,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Split a column into multiple columns.",
    fields: [
      { key: "source",     label: "Source column", kind: "text", required: true },
      { key: "separator",  label: "Separator",     kind: "text", default: " " },
    ],
    defaultData: { label: "Split Columns" },
  },
  deduplicate: {
    type: "deduplicate",
    label: "Deduplicate",
    category: "transformations",
    icon: Copy,
    bg: "bg-violet-50",
    accent: "text-violet-600 border-violet-300",
    description: "Remove duplicate records based on a key.",
    fields: [
      { key: "keyColumns", label: "Key columns", kind: "multiselect", default: [] },
      { key: "strategy",   label: "Keep",         kind: "select",
        options: [
          { value: "first", label: "First occurrence" },
          { value: "last",  label: "Last occurrence" },
        ], default: "first" },
    ],
    defaultData: { label: "Deduplicate" },
  },

  // ─────── QUALITY ───────
  validate: {
    type: "validate",
    label: "Validate",
    category: "quality",
    icon: ListChecks,
    bg: "bg-amber-50",
    accent: "text-amber-600 border-amber-300",
    description: "Validate the converted output against FBDI rules.",
    fields: [
      { key: "stopOnError", label: "Stop on error", kind: "select",
        options: [{ value: "yes", label: "Yes" }, { value: "no", label: "No (continue)" }], default: "no" },
    ],
    runnable: true,
    defaultData: { label: "Validate" },
  },

  // ─────── OUTPUT ───────
  preview_output: {
    type: "preview_output",
    label: "Preview Output",
    category: "output",
    icon: Eye,
    bg: "bg-rose-50",
    accent: "text-rose-600 border-rose-300",
    description: "Preview the converted FBDI output and column lineage.",
    fields: [
      { key: "rows", label: "Preview rows", kind: "number", default: 50 },
    ],
    runnable: true,
    defaultData: { label: "Preview Output" },
  },
  generate_fbdi: {
    type: "generate_fbdi",
    label: "Generate FBDI",
    category: "output",
    icon: FileOutput,
    bg: "bg-rose-50",
    accent: "text-rose-600 border-rose-300",
    description: "Build the Fusion-ready FBDI artefact (CSV/XLSX).",
    fields: [
      { key: "format", label: "Format", kind: "select",
        options: [{ value: "csv", label: "CSV" }, { value: "xlsx", label: "XLSX" }], default: "csv" },
    ],
    defaultData: { label: "Generate FBDI" },
  },
  load_to_fusion: {
    type: "load_to_fusion",
    label: "Load to Fusion",
    category: "output",
    icon: CloudUpload,
    bg: "bg-rose-50",
    accent: "text-rose-600 border-rose-300",
    description: "Submit (or simulate) the load to Oracle Fusion.",
    fields: [
      { key: "mode", label: "Mode", kind: "select",
        options: [
          { value: "simulate", label: "Simulate" },
          { value: "live",     label: "Live (placeholder)" },
        ], default: "simulate" },
    ],
    runnable: true,
    defaultData: { label: "Load to Fusion" },
  },
};

/** Group node types by category for the palette. */
export function groupedNodeTypes(): Record<NodeCategory, NodeTypeDef[]> {
  const out = {} as Record<NodeCategory, NodeTypeDef[]>;
  for (const cat of NODE_CATEGORIES) out[cat.key] = [];
  for (const def of Object.values(NODE_TYPES)) out[def.category].push(def);
  return out;
}

/** Resolve a default data blob for a freshly-dragged node. */
export function defaultNodeData(typeKey: string): Record<string, any> {
  const def = NODE_TYPES[typeKey];
  if (!def) return { label: typeKey };
  const data: Record<string, any> = { ...(def.defaultData || {}), nodeType: typeKey };
  for (const f of def.fields || []) {
    if (f.default !== undefined) data[f.key] = f.default;
  }
  return data;
}
