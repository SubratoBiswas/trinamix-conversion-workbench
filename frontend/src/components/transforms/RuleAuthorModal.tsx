import React, { useEffect, useMemo, useState } from "react";
import {
  Plus, Trash2, Wand2, Check, AlertTriangle, Code2, Eye,
} from "lucide-react";
import { MappingApi } from "@/api";
import { Button, Modal, Pill } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { DatasetDetail, FBDIField } from "@/types";

/**
 * Universal rule authoring modal — one place to add any of the engine's
 * transformation rule types with a typed form and a live preview against
 * the conversion's dataset. Used from Transformation Studio, Mapping
 * Inspector, and the Recommendations Hub.
 */

type Cfg = Record<string, any>;

interface RuleSpec {
  label: string;
  description: string;
  defaultConfig: () => Cfg;
  rowAware?: boolean;            // hides the source-column requirement when true
  needsSourceColumn?: boolean;   // CONSTANT / COMPUTED don't need one
  Form: React.FC<FormProps>;
}

interface FormProps {
  config: Cfg;
  setConfig: (c: Cfg) => void;
  sources: { name: string }[];
}

// ─────────────────────────────────────────────────────────────────────
// Per-rule-type forms
// ─────────────────────────────────────────────────────────────────────

const NoConfig: React.FC<FormProps> = () => (
  <div className="text-xs text-ink-muted">No configuration needed.</div>
);

const KeepCharsForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <Field label="Characters to keep (besides letters/digits/space)">
    <input
      className="input"
      value={config.keep ?? ""}
      onChange={(e) => setConfig({ ...config, keep: e.target.value })}
      placeholder="-_./"
    />
  </Field>
);

const ReplaceForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-2 gap-3">
    <Field label="Find">
      <input
        className="input"
        value={config.find ?? ""}
        onChange={(e) => setConfig({ ...config, find: e.target.value })}
      />
    </Field>
    <Field label="Replace with">
      <input
        className="input"
        value={config.replace ?? ""}
        onChange={(e) => setConfig({ ...config, replace: e.target.value })}
      />
    </Field>
  </div>
);

const RegexReplaceForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="space-y-3">
    <Field label="Regex pattern" hint="JavaScript / Python regex syntax">
      <input
        className="input font-mono"
        value={config.pattern ?? ""}
        onChange={(e) => setConfig({ ...config, pattern: e.target.value })}
        placeholder="^0+"
      />
    </Field>
    <div className="grid grid-cols-2 gap-3">
      <Field label="Replace with" hint={"Use \\1, \\2 for capture groups"}>
        <input
          className="input font-mono"
          value={config.replace ?? ""}
          onChange={(e) => setConfig({ ...config, replace: e.target.value })}
        />
      </Field>
      <Field label="Flags" hint="i = ignore case, m = multiline">
        <input
          className="input font-mono"
          value={config.flags ?? ""}
          onChange={(e) => setConfig({ ...config, flags: e.target.value })}
          placeholder="i"
        />
      </Field>
    </div>
  </div>
);

const RegexExtractForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="space-y-3">
    <Field label="Pattern" hint="Capture groups become extractable">
      <input
        className="input font-mono"
        value={config.pattern ?? ""}
        onChange={(e) => setConfig({ ...config, pattern: e.target.value })}
        placeholder="ITEM-(\\d+)"
      />
    </Field>
    <div className="grid grid-cols-2 gap-3">
      <Field label="Capture group" hint="0 = full match, 1 = first group">
        <input
          type="number"
          className="input"
          value={config.group ?? 1}
          onChange={(e) => setConfig({ ...config, group: Number(e.target.value) })}
        />
      </Field>
      <Field label="Default if no match">
        <input
          className="input"
          value={config.default ?? ""}
          onChange={(e) => setConfig({ ...config, default: e.target.value })}
        />
      </Field>
    </div>
  </div>
);

const PadForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-3 gap-3">
    <Field label="Side">
      <select
        className="input"
        value={config.side ?? "left"}
        onChange={(e) => setConfig({ ...config, side: e.target.value })}
      >
        <option value="left">Left (zero-pad)</option>
        <option value="right">Right</option>
      </select>
    </Field>
    <Field label="Total length">
      <input
        type="number"
        className="input"
        value={config.length ?? 8}
        onChange={(e) => setConfig({ ...config, length: Number(e.target.value) })}
      />
    </Field>
    <Field label="Pad char">
      <input
        className="input font-mono"
        maxLength={1}
        value={config.char ?? "0"}
        onChange={(e) => setConfig({ ...config, char: e.target.value })}
      />
    </Field>
  </div>
);

const SubstringForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-2 gap-3">
    <Field label="Start (0-based)">
      <input
        type="number"
        className="input"
        value={config.start ?? 0}
        onChange={(e) => setConfig({ ...config, start: Number(e.target.value) })}
      />
    </Field>
    <Field label="Length (blank = to end)">
      <input
        type="number"
        className="input"
        value={config.length ?? ""}
        onChange={(e) =>
          setConfig({
            ...config,
            length: e.target.value === "" ? "" : Number(e.target.value),
          })
        }
      />
    </Field>
  </div>
);

const SingleValueForm = (label: string, hint?: string): React.FC<FormProps> =>
  function SVF({ config, setConfig }) {
    return (
      <Field label={label} hint={hint}>
        <input
          className="input"
          value={config.value ?? ""}
          onChange={(e) => setConfig({ ...config, value: e.target.value })}
        />
      </Field>
    );
  };

const ValueMapForm: React.FC<FormProps> = ({ config, setConfig }) => {
  const reserved = new Set(["case_insensitive", "default"]);
  const rows = Object.entries(config).filter(([k]) => !reserved.has(k));

  const setRows = (next: [string, string][]) => {
    const out: Cfg = {
      case_insensitive: config.case_insensitive ?? true,
      ...(config.default !== undefined ? { default: config.default } : {}),
    };
    next.forEach(([k, v]) => {
      if (k) out[k] = v;
    });
    setConfig(out);
  };

  const update = (i: number, key: string, value: string) => {
    const next = rows.slice() as [string, string][];
    if (key === "from") next[i] = [value, next[i][1]];
    else next[i] = [next[i][0], value];
    setRows(next);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
          From → To pairs
        </span>
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={config.case_insensitive ?? true}
            onChange={(e) =>
              setConfig({ ...config, case_insensitive: e.target.checked })
            }
          />
          Case-insensitive
        </label>
      </div>
      <div className="space-y-1">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              className="input flex-1 font-mono"
              value={k}
              onChange={(e) => update(i, "from", e.target.value)}
              placeholder="active"
            />
            <span className="text-ink-muted">→</span>
            <input
              className="input flex-1 font-mono"
              value={String(v ?? "")}
              onChange={(e) => update(i, "to", e.target.value)}
              placeholder="production"
            />
            <button
              onClick={() => setRows(rows.filter((_, j) => j !== i) as [string, string][])}
              className="rounded p-1.5 text-ink-subtle hover:bg-canvas hover:text-danger"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      <button
        onClick={() => setRows([...rows, ["", ""]] as [string, string][])}
        className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
      >
        <Plus className="h-3 w-3" /> Add pair
      </button>
      <Field label="Default if no match (optional)">
        <input
          className="input"
          value={String(config.default ?? "")}
          onChange={(e) =>
            setConfig({
              ...config,
              default: e.target.value === "" ? undefined : e.target.value,
            })
          }
          placeholder="Leave blank to pass through unchanged"
        />
      </Field>
    </div>
  );
};

const DATE_FORMATS = [
  ["%Y-%m-%d", "YYYY-MM-DD (ISO)"],
  ["%Y/%m/%d", "YYYY/MM/DD (FBDI)"],
  ["%m/%d/%Y", "MM/DD/YYYY (US)"],
  ["%d/%m/%Y", "DD/MM/YYYY (EU)"],
  ["%d-%b-%Y", "DD-Mon-YYYY"],
  ["%Y%m%d", "YYYYMMDD"],
  ["%m-%d-%Y", "MM-DD-YYYY"],
];

const DateFormatForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-2 gap-3">
    <Field label="Source format">
      <select
        className="input"
        value={config.input_format ?? "%m/%d/%Y"}
        onChange={(e) =>
          setConfig({ ...config, input_format: e.target.value })
        }
      >
        {DATE_FORMATS.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>
    </Field>
    <Field label="Target format">
      <select
        className="input"
        value={config.output_format ?? "%Y/%m/%d"}
        onChange={(e) =>
          setConfig({ ...config, output_format: e.target.value })
        }
      >
        {DATE_FORMATS.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>
    </Field>
  </div>
);

const NumberFormatForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <Field label="Decimal places">
    <input
      type="number"
      className="input"
      value={config.decimals ?? 2}
      onChange={(e) => setConfig({ ...config, decimals: Number(e.target.value) })}
    />
  </Field>
);

const ARITH_OPS = [
  ["multiply", "Multiply by"],
  ["divide", "Divide by"],
  ["add", "Add"],
  ["subtract", "Subtract"],
  ["round", "Round to N decimals"],
  ["abs", "Absolute value"],
  ["negate", "Negate"],
];

const ArithmeticForm: React.FC<FormProps> = ({ config, setConfig }) => {
  const op = config.op ?? "multiply";
  const needsAmount = ["multiply", "divide", "add", "subtract"].includes(op);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Operation">
          <select
            className="input"
            value={op}
            onChange={(e) => setConfig({ ...config, op: e.target.value })}
          >
            {ARITH_OPS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        {needsAmount && (
          <Field label="Amount">
            <input
              type="number"
              step="any"
              className="input"
              value={config.amount ?? ""}
              onChange={(e) =>
                setConfig({ ...config, amount: Number(e.target.value) })
              }
            />
          </Field>
        )}
      </div>
      <Field label="Round to N decimals (optional)">
        <input
          type="number"
          className="input"
          value={config.decimals ?? ""}
          onChange={(e) =>
            setConfig({
              ...config,
              decimals: e.target.value === "" ? undefined : Number(e.target.value),
            })
          }
        />
      </Field>
    </div>
  );
};

const ColumnPicker: React.FC<{
  value: string[];
  onChange: (v: string[]) => void;
  sources: { name: string }[];
  hint?: string;
}> = ({ value, onChange, sources, hint }) => (
  <div className="space-y-1.5">
    <div className="flex flex-wrap gap-1">
      {value.map((c, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-full bg-brand-subtle px-2 py-0.5 font-mono text-[11px] text-brand-dark"
        >
          {c}
          <button
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            className="rounded-full hover:bg-white/40"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </span>
      ))}
    </div>
    <select
      className="input"
      value=""
      onChange={(e) => {
        if (e.target.value) onChange([...value, e.target.value]);
      }}
    >
      <option value="">+ Add column…</option>
      {sources
        .filter((s) => !value.includes(s.name))
        .map((s) => (
          <option key={s.name} value={s.name}>
            {s.name}
          </option>
        ))}
    </select>
    {hint && <div className="text-[10.5px] text-ink-muted">{hint}</div>}
  </div>
);

const ConcatForm: React.FC<FormProps> = ({ config, setConfig, sources }) => (
  <div className="space-y-3">
    <Field label="Columns to concatenate (in order)">
      <ColumnPicker
        value={config.columns ?? []}
        onChange={(cs) => setConfig({ ...config, columns: cs })}
        sources={sources}
      />
    </Field>
    <Field label="Separator">
      <input
        className="input font-mono"
        value={config.separator ?? " "}
        onChange={(e) => setConfig({ ...config, separator: e.target.value })}
      />
    </Field>
  </div>
);

const SplitForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-2 gap-3">
    <Field label="Separator">
      <input
        className="input font-mono"
        value={config.separator ?? " "}
        onChange={(e) => setConfig({ ...config, separator: e.target.value })}
      />
    </Field>
    <Field label="Take piece (0-based)">
      <input
        type="number"
        className="input"
        value={config.index ?? 0}
        onChange={(e) => setConfig({ ...config, index: Number(e.target.value) })}
      />
    </Field>
  </div>
);

const CoalesceForm: React.FC<FormProps> = ({ config, setConfig, sources }) => (
  <div className="space-y-3">
    <Field
      label="Columns checked in order"
      hint="First non-blank value wins"
    >
      <ColumnPicker
        value={config.columns ?? []}
        onChange={(cs) => setConfig({ ...config, columns: cs })}
        sources={sources}
      />
    </Field>
    <Field label="Default if all blank">
      <input
        className="input"
        value={config.default ?? ""}
        onChange={(e) => setConfig({ ...config, default: e.target.value })}
      />
    </Field>
  </div>
);

const ConditionalForm: React.FC<FormProps> = ({ config, setConfig, sources }) => (
  <div className="space-y-3">
    <div className="grid grid-cols-2 gap-3">
      <Field label="If column">
        <select
          className="input"
          value={config.if_column ?? ""}
          onChange={(e) => setConfig({ ...config, if_column: e.target.value })}
        >
          <option value="">— pick —</option>
          {sources.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Equals">
        <input
          className="input font-mono"
          value={config.equals ?? ""}
          onChange={(e) => setConfig({ ...config, equals: e.target.value })}
        />
      </Field>
    </div>
    <div className="grid grid-cols-2 gap-3">
      <Field label="Then">
        <input
          className="input"
          value={config.then ?? ""}
          onChange={(e) => setConfig({ ...config, then: e.target.value })}
        />
      </Field>
      <Field label="Else">
        <input
          className="input"
          value={config.else ?? ""}
          onChange={(e) => setConfig({ ...config, else: e.target.value })}
        />
      </Field>
    </div>
  </div>
);

const CASE_OPS = [
  ["eq", "equals"],
  ["neq", "not equals"],
  ["gt", ">"],
  ["gte", "≥"],
  ["lt", "<"],
  ["lte", "≤"],
  ["in", "in (comma list)"],
  ["notin", "not in (comma list)"],
  ["contains", "contains"],
  ["startswith", "starts with"],
  ["endswith", "ends with"],
  ["regex", "matches regex"],
  ["isblank", "is blank"],
  ["notblank", "is not blank"],
];

const CaseWhenForm: React.FC<FormProps> = ({ config, setConfig, sources }) => {
  const branches: any[] = config.branches ?? [];
  const update = (i: number, patch: any) => {
    const next = branches.slice();
    next[i] = { ...next[i], ...patch };
    setConfig({ ...config, branches: next });
  };
  const remove = (i: number) =>
    setConfig({ ...config, branches: branches.filter((_, j) => j !== i) });
  const add = () =>
    setConfig({
      ...config,
      branches: [
        ...branches,
        { if_column: sources[0]?.name ?? "", op: "eq", value: "", then: "" },
      ],
    });
  return (
    <div className="space-y-2">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
        When … then …
      </div>
      {branches.map((br, i) => {
        const noValueOp = ["isblank", "notblank"].includes(br.op);
        return (
          <div
            key={i}
            className="grid grid-cols-[1fr_auto_1fr_auto_1fr_auto] items-center gap-1.5"
          >
            <select
              className="input !text-xs"
              value={br.if_column ?? ""}
              onChange={(e) => update(i, { if_column: e.target.value })}
            >
              <option value="">— column —</option>
              {sources.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
            <select
              className="input !w-auto !text-xs"
              value={br.op ?? "eq"}
              onChange={(e) => update(i, { op: e.target.value })}
            >
              {CASE_OPS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
            <input
              className="input !text-xs font-mono"
              value={noValueOp ? "" : br.value ?? ""}
              disabled={noValueOp}
              placeholder={noValueOp ? "—" : "value"}
              onChange={(e) => update(i, { value: e.target.value })}
            />
            <span className="text-[10.5px] text-ink-muted">→</span>
            <input
              className="input !text-xs"
              value={br.then ?? ""}
              onChange={(e) => update(i, { then: e.target.value })}
              placeholder="then"
            />
            <button
              onClick={() => remove(i)}
              className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
      <button
        onClick={add}
        className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
      >
        <Plus className="h-3 w-3" /> Add branch
      </button>
      <Field label="Default (none of the above match)">
        <input
          className="input"
          value={config.default ?? ""}
          onChange={(e) => setConfig({ ...config, default: e.target.value })}
        />
      </Field>
    </div>
  );
};

const COMPUTED_SOURCES = [
  ["today", "Today's date"],
  ["now", "Current timestamp"],
  ["row_index", "Row number (1-based)"],
  ["uuid", "Random UUID"],
  ["current_user", "Current user email"],
];

const ComputedForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="grid grid-cols-2 gap-3">
    <Field label="Source">
      <select
        className="input"
        value={config.source ?? "today"}
        onChange={(e) => setConfig({ ...config, source: e.target.value })}
      >
        {COMPUTED_SOURCES.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>
    </Field>
    {(config.source === "today" || config.source === "now" || !config.source) && (
      <Field label="Date format">
        <select
          className="input"
          value={
            config.format ??
            (config.source === "now" ? "%Y/%m/%d %H:%M:%S" : "%Y/%m/%d")
          }
          onChange={(e) => setConfig({ ...config, format: e.target.value })}
        >
          {DATE_FORMATS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </Field>
    )}
  </div>
);

const CrosswalkForm: React.FC<FormProps> = ({ config, setConfig }) => (
  <div className="space-y-3">
    <Field
      label="Crosswalk name"
      hint="Must match a crosswalk loaded for this conversion"
    >
      <input
        className="input"
        value={config.crosswalk ?? ""}
        onChange={(e) => setConfig({ ...config, crosswalk: e.target.value })}
        placeholder="uom_map"
      />
    </Field>
    <Field label="Default if not found">
      <input
        className="input"
        value={config.default ?? ""}
        onChange={(e) => setConfig({ ...config, default: e.target.value })}
      />
    </Field>
  </div>
);

// ─────────────────────────────────────────────────────────────────────
// Spec registry
// ─────────────────────────────────────────────────────────────────────

const RULE_SPECS: Record<string, RuleSpec> = {
  // Format
  TRIM: { label: "Trim whitespace", description: "Strip leading/trailing spaces", defaultConfig: () => ({}), Form: NoConfig },
  UPPERCASE: { label: "Uppercase", description: "Convert to upper case", defaultConfig: () => ({}), Form: NoConfig },
  LOWERCASE: { label: "Lowercase", description: "Convert to lower case", defaultConfig: () => ({}), Form: NoConfig },
  TITLE_CASE: { label: "Title case", description: "Capitalise first letter of each word", defaultConfig: () => ({}), Form: NoConfig },
  REMOVE_HYPHEN: { label: "Remove hyphens", description: "Strip all '-' characters", defaultConfig: () => ({}), Form: NoConfig },
  REMOVE_SPECIAL_CHARS: { label: "Remove special characters", description: "Keep alphanumerics + spaces", defaultConfig: () => ({ keep: "" }), Form: KeepCharsForm },
  REPLACE: { label: "Find & replace (literal)", description: "Substring replacement", defaultConfig: () => ({ find: "", replace: "" }), Form: ReplaceForm },
  REGEX_REPLACE: { label: "Find & replace (regex)", description: "Pattern-based replacement", defaultConfig: () => ({ pattern: "", replace: "", flags: "" }), Form: RegexReplaceForm },
  REGEX_EXTRACT: { label: "Regex extract", description: "Pull a capture group from the value", defaultConfig: () => ({ pattern: "", group: 1, default: "" }), Form: RegexExtractForm },
  PAD: { label: "Pad to length", description: "Left/right pad to N characters", defaultConfig: () => ({ side: "left", length: 8, char: "0" }), Form: PadForm },
  SUBSTRING: { label: "Substring", description: "Slice characters from the value", defaultConfig: () => ({ start: 0, length: "" }), Form: SubstringForm },
  // Default & computed
  DEFAULT_VALUE: { label: "Default if blank", description: "Fill empty source values with a default", defaultConfig: () => ({ value: "" }), Form: SingleValueForm("Default value") },
  CONSTANT: { label: "Constant value", description: "Always emit this value, ignoring the source", defaultConfig: () => ({ value: "" }), needsSourceColumn: false, Form: SingleValueForm("Always set to") },
  COMPUTED: { label: "Computed value", description: "Today, sequence number, UUID, current user", defaultConfig: () => ({ source: "today", format: "%Y/%m/%d" }), needsSourceColumn: false, Form: ComputedForm },
  // Mapping
  VALUE_MAP: { label: "Value mapping", description: "Replace values using a from→to dictionary", defaultConfig: () => ({ active: "production", inactive: "discontinued", case_insensitive: true }), Form: ValueMapForm },
  CROSSWALK_LOOKUP: { label: "Crosswalk lookup", description: "Resolve via a named lookup table loaded for this conversion", defaultConfig: () => ({ crosswalk: "", default: "" }), Form: CrosswalkForm },
  // Date / number
  DATE_FORMAT: { label: "Date format", description: "Convert dates between formats", defaultConfig: () => ({ input_format: "%m/%d/%Y", output_format: "%Y/%m/%d" }), Form: DateFormatForm },
  NUMBER_FORMAT: { label: "Number format", description: "Round to N decimals", defaultConfig: () => ({ decimals: 2 }), Form: NumberFormatForm },
  ARITHMETIC: { label: "Arithmetic", description: "Multiply / divide / round / abs", defaultConfig: () => ({ op: "multiply", amount: 1 }), Form: ArithmeticForm },
  // Multi-column
  CONCAT: { label: "Concatenate columns", description: "Join multiple source columns into one", defaultConfig: () => ({ columns: [], separator: " " }), rowAware: true, needsSourceColumn: false, Form: ConcatForm },
  SPLIT: { label: "Split & take piece", description: "Split on separator and take Nth element", defaultConfig: () => ({ separator: " ", index: 0 }), Form: SplitForm },
  COALESCE: { label: "Coalesce (first non-blank)", description: "Pick the first non-blank column from a list", defaultConfig: () => ({ columns: [], default: "" }), rowAware: true, needsSourceColumn: false, Form: CoalesceForm },
  // Conditional
  CONDITIONAL: { label: "If / then / else (single)", description: "Single equality check across two columns", defaultConfig: () => ({ if_column: "", equals: "", then: "", else: "" }), rowAware: true, Form: ConditionalForm },
  CASE_WHEN: { label: "Case / when (multi-branch)", description: "Multiple conditions with comparison ops", defaultConfig: () => ({ branches: [], default: "" }), rowAware: true, Form: CaseWhenForm },
};

const RULE_GROUPS: { label: string; types: string[] }[] = [
  { label: "Text format", types: ["TRIM", "UPPERCASE", "LOWERCASE", "TITLE_CASE", "REMOVE_HYPHEN", "REMOVE_SPECIAL_CHARS", "REPLACE", "REGEX_REPLACE", "REGEX_EXTRACT", "PAD", "SUBSTRING"] },
  { label: "Default & computed", types: ["DEFAULT_VALUE", "CONSTANT", "COMPUTED"] },
  { label: "Value mapping", types: ["VALUE_MAP", "CROSSWALK_LOOKUP"] },
  { label: "Date & number", types: ["DATE_FORMAT", "NUMBER_FORMAT", "ARITHMETIC"] },
  { label: "Multi-column", types: ["CONCAT", "SPLIT", "COALESCE"] },
  { label: "Conditional", types: ["CONDITIONAL", "CASE_WHEN"] },
];

// ─────────────────────────────────────────────────────────────────────
// Modal
// ─────────────────────────────────────────────────────────────────────

interface RuleAuthorModalProps {
  open: boolean;
  onClose: () => void;
  conversionId: number;
  fields: FBDIField[];
  sourceColumns: DatasetDetail["columns"];
  defaultTargetFieldId?: number | null;
  defaultSourceColumn?: string | null;
  onSaved: () => void;
}

export const RuleAuthorModal: React.FC<RuleAuthorModalProps> = ({
  open,
  onClose,
  conversionId,
  fields,
  sourceColumns,
  defaultTargetFieldId,
  defaultSourceColumn,
  onSaved,
}) => {
  const [type, setType] = useState<string>("VALUE_MAP");
  const [targetFieldId, setTargetFieldId] = useState<number | null>(
    defaultTargetFieldId ?? null
  );
  const [sourceColumn, setSourceColumn] = useState<string>(
    defaultSourceColumn ?? ""
  );
  const [config, setConfig] = useState<Cfg>(RULE_SPECS.VALUE_MAP.defaultConfig());
  const [description, setDescription] = useState<string>("");
  const [advanced, setAdvanced] = useState(false);
  const [advancedRaw, setAdvancedRaw] = useState<string>("{}");
  const [advancedError, setAdvancedError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Live preview
  const [preview, setPreview] = useState<
    { source: any; output: any; error?: string | null }[] | null
  >(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Reset on open
  useEffect(() => {
    if (!open) return;
    setType("VALUE_MAP");
    setTargetFieldId(defaultTargetFieldId ?? null);
    setSourceColumn(defaultSourceColumn ?? "");
    setConfig(RULE_SPECS.VALUE_MAP.defaultConfig());
    setDescription("");
    setAdvanced(false);
    setAdvancedRaw("{}");
    setAdvancedError(null);
    setSaveError(null);
    setPreview(null);
  }, [open]);

  // Default config swap on type change
  const onTypeChange = (next: string) => {
    setType(next);
    const cfg = RULE_SPECS[next].defaultConfig();
    setConfig(cfg);
    setAdvancedRaw(JSON.stringify(cfg, null, 2));
  };

  const spec = RULE_SPECS[type];

  // Sync advanced JSON ↔ form config
  useEffect(() => {
    if (advanced) setAdvancedRaw(JSON.stringify(config, null, 2));
  }, [config, advanced]);

  // Debounced live preview
  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(async () => {
      try {
        let activeCfg = config;
        if (advanced) {
          try {
            activeCfg = JSON.parse(advancedRaw);
            setAdvancedError(null);
          } catch (e: any) {
            setAdvancedError("Config must be valid JSON");
            return;
          }
        }
        const res = await MappingApi.previewRules(conversionId, {
          source_column: sourceColumn || undefined,
          rules: [{ rule_type: type, config: activeCfg }],
          sample_size: 5,
        });
        setPreview(res.samples);
        setPreviewError(null);
      } catch (e: any) {
        setPreviewError(
          e?.response?.data?.detail || e?.message || "Preview failed"
        );
        setPreview(null);
      }
    }, 350);
    return () => clearTimeout(handle);
  }, [open, conversionId, type, config, sourceColumn, advanced, advancedRaw]);

  const save = async () => {
    setSaveError(null);
    let activeCfg = config;
    if (advanced) {
      try {
        activeCfg = JSON.parse(advancedRaw);
      } catch {
        setAdvancedError("Config must be valid JSON");
        return;
      }
    }
    if (!targetFieldId) {
      setSaveError("Pick a target FBDI field for this rule.");
      return;
    }
    setSaving(true);
    try {
      await MappingApi.addRule(conversionId, {
        target_field_id: targetFieldId,
        source_column: sourceColumn || undefined,
        rule_type: type,
        rule_config: activeCfg,
        description: description || undefined,
      });
      onSaved();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add transformation rule"
      size="xl"
      footer={
        <div className="flex w-full items-center justify-between">
          <button
            onClick={() => setAdvanced((a) => !a)}
            className={cn(
              "inline-flex items-center gap-1 text-xs font-medium",
              advanced ? "text-brand-dark" : "text-ink-muted hover:text-ink"
            )}
          >
            <Code2 className="h-3.5 w-3.5" />
            {advanced ? "Form view" : "Advanced (JSON)"}
          </button>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={save} loading={saving}>
              <Check className="h-3.5 w-3.5" /> Save & learn
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid grid-cols-[1fr_320px] gap-5">
        {/* Left: form */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Rule type">
              <select
                className="input"
                value={type}
                onChange={(e) => onTypeChange(e.target.value)}
              >
                {RULE_GROUPS.map((g) => (
                  <optgroup key={g.label} label={g.label}>
                    {g.types.map((t) => (
                      <option key={t} value={t}>
                        {RULE_SPECS[t].label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <div className="mt-1 text-[11px] text-ink-muted">
                {spec.description}
              </div>
            </Field>
            <Field label="Target FBDI field" required>
              <select
                className="input"
                value={targetFieldId ?? ""}
                onChange={(e) =>
                  setTargetFieldId(e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">— pick a target field —</option>
                {fields.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.field_name}
                    {f.required ? " *" : ""}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {spec.needsSourceColumn !== false && (
            <Field
              label="Source column"
              hint={
                spec.rowAware
                  ? "Optional — this rule reads other columns from the row"
                  : "The legacy column this rule transforms"
              }
            >
              <select
                className="input"
                value={sourceColumn}
                onChange={(e) => setSourceColumn(e.target.value)}
              >
                <option value="">— none —</option>
                {sourceColumns.map((c) => (
                  <option key={c.id} value={c.column_name}>
                    {c.column_name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          <div className="rounded-md border border-line bg-canvas p-3">
            {advanced ? (
              <>
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                    Config (JSON)
                  </span>
                  {advancedError && (
                    <span className="text-[11px] text-danger">{advancedError}</span>
                  )}
                </div>
                <textarea
                  className="input min-h-[180px] font-mono text-xs"
                  value={advancedRaw}
                  onChange={(e) => setAdvancedRaw(e.target.value)}
                />
              </>
            ) : (
              <>
                <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                  {spec.label}
                </div>
                <spec.Form
                  config={config}
                  setConfig={setConfig}
                  sources={sourceColumns.map((c) => ({ name: c.column_name }))}
                />
              </>
            )}
          </div>

          <Field label="Description (optional)">
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Why this rule exists"
            />
          </Field>

          {saveError && (
            <div className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              {saveError}
            </div>
          )}
        </div>

        {/* Right: live preview */}
        <aside className="rounded-md border border-line bg-canvas">
          <div className="flex items-center gap-1.5 border-b border-line px-3 py-2">
            <Eye className="h-3.5 w-3.5 text-brand-dark" />
            <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink">
              Live preview
            </span>
            <Pill tone="info" className="!text-[9px]">
              {sourceColumn || (spec.rowAware ? "row-aware" : "no source")}
            </Pill>
          </div>
          <div className="p-3 text-xs">
            {previewError ? (
              <div className="rounded bg-danger-subtle px-2 py-1.5 text-[11px] text-danger">
                {previewError}
              </div>
            ) : preview === null ? (
              <div className="text-ink-muted">Computing preview…</div>
            ) : preview.length === 0 ? (
              <div className="text-ink-muted">No sample rows in dataset.</div>
            ) : (
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-ink-muted">
                    <th className="pb-1.5 pr-2 font-medium">Source</th>
                    <th className="pb-1.5 font-medium">Output</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.map((s, i) => (
                    <tr key={i} className="border-t border-line/60 align-top">
                      <td className="py-1.5 pr-2 font-mono text-ink">
                        {String(s.source ?? "")}
                      </td>
                      <td className="py-1.5 font-mono">
                        {s.error ? (
                          <span className="text-danger">{s.error}</span>
                        ) : (
                          <span className="text-success">
                            {String(s.output ?? "")}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="border-t border-line bg-white px-3 py-2 text-[10.5px] text-ink-muted">
            <Wand2 className="mr-1 inline h-3 w-3" />
            Saved rules apply during <em>Generate Output</em> and land in the Rule Library.
          </div>
        </aside>
      </div>
    </Modal>
  );
};

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

const Field: React.FC<{
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}> = ({ label, hint, required, children }) => (
  <div>
    <label className="label">
      {label}
      {required && <span className="ml-1 text-danger">*</span>}
    </label>
    {children}
    {hint && <div className="mt-0.5 text-[10.5px] text-ink-muted">{hint}</div>}
  </div>
);
