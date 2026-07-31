import React, { useEffect, useMemo, useState } from "react";
import {
  Plus, Trash2, Wand2, Check, AlertTriangle, Code2, Eye,
  Sparkles, MessageSquare, ChevronDown, ChevronRight,
} from "lucide-react";
import { MappingApi } from "@/api";
import { Button, Modal, Pill } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { DatasetDetail, FBDIField, TransformationRule } from "@/types";

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

// CASE_WHEN branches accept two shapes (the engine handles both):
//   simple:   {if_column, op, value, then}
//   compound: {all_of|any_of: [{column, op, value}, ...], then}
// The form lets each branch toggle between the two shapes — analysts
// authoring "if A is X and B is Y then ..." need the compound form, and
// translator output may produce either depending on the description.

type LeafCond = { column?: string; op?: string; value?: any };

const _isCompound = (br: any): boolean =>
  br && (Array.isArray(br.all_of) || Array.isArray(br.any_of));

const _branchCombinator = (br: any): "all_of" | "any_of" =>
  Array.isArray(br?.any_of) ? "any_of" : "all_of";

const _branchConditions = (br: any): LeafCond[] => {
  if (Array.isArray(br?.all_of)) return br.all_of;
  if (Array.isArray(br?.any_of)) return br.any_of;
  return [];
};

const CaseWhenForm: React.FC<FormProps> = ({ config, setConfig, sources }) => {
  const branches: any[] = config.branches ?? [];

  const update = (i: number, patch: any) => {
    const next = branches.slice();
    next[i] = { ...next[i], ...patch };
    setConfig({ ...config, branches: next });
  };
  const replace = (i: number, newBranch: any) => {
    const next = branches.slice();
    next[i] = newBranch;
    setConfig({ ...config, branches: next });
  };
  const remove = (i: number) =>
    setConfig({ ...config, branches: branches.filter((_, j) => j !== i) });
  const addSimple = () =>
    setConfig({
      ...config,
      branches: [
        ...branches,
        { if_column: sources[0]?.name ?? "", op: "eq", value: "", then: "" },
      ],
    });
  const addCompound = () =>
    setConfig({
      ...config,
      branches: [
        ...branches,
        {
          all_of: [
            { column: sources[0]?.name ?? "", op: "eq", value: "" },
            { column: sources[1]?.name ?? sources[0]?.name ?? "", op: "eq", value: "" },
          ],
          then: "",
        },
      ],
    });

  const toCompound = (i: number) => {
    const br = branches[i];
    const seed: LeafCond = {
      column: br?.if_column ?? sources[0]?.name ?? "",
      op: br?.op ?? "eq",
      value: br?.value ?? "",
    };
    replace(i, { all_of: [seed], then: br?.then ?? "" });
  };
  const toSimple = (i: number) => {
    const br = branches[i];
    const first = _branchConditions(br)[0] || {};
    replace(i, {
      if_column: first.column ?? "",
      op: first.op ?? "eq",
      value: first.value ?? "",
      then: br?.then ?? "",
    });
  };

  return (
    <div className="space-y-3">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
        When … then …
      </div>
      {branches.map((br, i) =>
        _isCompound(br) ? (
          <CompoundBranchEditor
            key={i} branch={br} sources={sources}
            onChange={(next) => replace(i, next)}
            onRemove={() => remove(i)}
            onSwitchToSimple={() => toSimple(i)}
          />
        ) : (
          <SimpleBranchEditor
            key={i} branch={br} sources={sources}
            onChange={(patch) => update(i, patch)}
            onRemove={() => remove(i)}
            onSwitchToCompound={() => toCompound(i)}
          />
        )
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={addSimple}
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
        >
          <Plus className="h-3 w-3" /> Add simple branch
        </button>
        <span className="text-[10.5px] text-ink-muted">·</span>
        <button
          onClick={addCompound}
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-dark hover:underline"
        >
          <Plus className="h-3 w-3" /> Add compound (AND/OR)
        </button>
      </div>
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

const _NO_VALUE_OPS = new Set(["isblank", "notblank"]);

const SimpleBranchEditor: React.FC<{
  branch: any;
  sources: { name: string }[];
  onChange: (patch: any) => void;
  onRemove: () => void;
  onSwitchToCompound: () => void;
}> = ({ branch, sources, onChange, onRemove, onSwitchToCompound }) => {
  const noValueOp = _NO_VALUE_OPS.has(branch.op);
  return (
    <div className="rounded-md border border-line bg-white p-2">
      <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr_auto] items-center gap-1.5">
        <select
          className="input !text-xs"
          value={branch.if_column ?? ""}
          onChange={(e) => onChange({ if_column: e.target.value })}
        >
          <option value="">— column —</option>
          {sources.map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        <select
          className="input !w-auto !text-xs"
          value={branch.op ?? "eq"}
          onChange={(e) => onChange({ op: e.target.value })}
        >
          {CASE_OPS.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <input
          className="input !text-xs font-mono"
          value={noValueOp ? "" : branch.value ?? ""}
          disabled={noValueOp}
          placeholder={noValueOp ? "—" : "value"}
          onChange={(e) => onChange({ value: e.target.value })}
        />
        <span className="text-[10.5px] text-ink-muted">→</span>
        <input
          className="input !text-xs"
          value={branch.then ?? ""}
          onChange={(e) => onChange({ then: e.target.value })}
          placeholder="then"
        />
        <button
          onClick={onRemove}
          className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
      <button
        onClick={onSwitchToCompound}
        className="mt-1 text-[10.5px] font-medium text-brand-dark hover:underline"
      >
        + add another condition (AND / OR)
      </button>
    </div>
  );
};

const CompoundBranchEditor: React.FC<{
  branch: any;
  sources: { name: string }[];
  onChange: (next: any) => void;
  onRemove: () => void;
  onSwitchToSimple: () => void;
}> = ({ branch, sources, onChange, onRemove, onSwitchToSimple }) => {
  const combinator = _branchCombinator(branch);
  const conditions = _branchConditions(branch);

  const setCombinator = (next: "all_of" | "any_of") => {
    const { all_of: _a, any_of: _o, ...rest } = branch;
    onChange({ ...rest, [next]: conditions });
  };
  const updateCondition = (i: number, patch: LeafCond) => {
    const next = conditions.slice();
    next[i] = { ...next[i], ...patch };
    onChange({ ...branch, [combinator]: next });
  };
  const removeCondition = (i: number) => {
    const next = conditions.filter((_, j) => j !== i);
    if (next.length <= 1) {
      // Collapse back to simple branch when only one condition remains —
      // less visual noise and the engine reads it the same.
      onSwitchToSimple();
      return;
    }
    onChange({ ...branch, [combinator]: next });
  };
  const addCondition = () => {
    onChange({
      ...branch,
      [combinator]: [
        ...conditions,
        { column: sources[0]?.name ?? "", op: "eq", value: "" },
      ],
    });
  };

  return (
    <div className="rounded-md border-2 border-brand/30 bg-brand-subtle/15 p-2">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="inline-flex items-center gap-1 rounded-md border border-line bg-white p-0.5 text-[10.5px] font-medium">
          {(["all_of", "any_of"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setCombinator(k)}
              className={cn(
                "rounded px-1.5 py-0.5",
                combinator === k ? "bg-brand text-white" : "text-ink-muted hover:text-ink",
              )}
            >
              {k === "all_of" ? "ALL of (AND)" : "ANY of (OR)"}
            </button>
          ))}
        </div>
        <button
          onClick={onRemove}
          className="rounded p-1 text-ink-subtle hover:bg-white hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="space-y-1.5">
        {conditions.map((cond, ci) => {
          const noVal = _NO_VALUE_OPS.has(cond.op || "");
          return (
            <div
              key={ci}
              className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-1.5"
            >
              <select
                className="input !text-xs"
                value={cond.column ?? ""}
                onChange={(e) => updateCondition(ci, { column: e.target.value })}
              >
                <option value="">— column —</option>
                {sources.map((s) => (
                  <option key={s.name} value={s.name}>{s.name}</option>
                ))}
              </select>
              <select
                className="input !w-auto !text-xs"
                value={cond.op ?? "eq"}
                onChange={(e) => updateCondition(ci, { op: e.target.value })}
              >
                {CASE_OPS.map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
              <input
                className="input !text-xs font-mono"
                value={noVal ? "" : (cond.value ?? "")}
                disabled={noVal}
                placeholder={noVal ? "—" : "value"}
                onChange={(e) => updateCondition(ci, { value: e.target.value })}
              />
              <button
                onClick={() => removeCondition(ci)}
                className="rounded p-1 text-ink-subtle hover:bg-white hover:text-danger"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
      <button
        onClick={addCondition}
        className="mt-1.5 inline-flex items-center gap-1 text-[10.5px] font-medium text-brand-dark hover:underline"
      >
        <Plus className="h-3 w-3" /> add condition
      </button>
      <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-2">
        <span className="text-[10.5px] font-medium text-ink-muted">then →</span>
        <input
          className="input !text-xs"
          value={branch.then ?? ""}
          onChange={(e) => onChange({ ...branch, then: e.target.value })}
          placeholder="value when this branch matches"
        />
      </div>
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
  // Types the BACKEND accepts that have no bespoke form here. They are seeded by
  // the supplier and customer rule catalogs (PHONE_PART, PREFIX, SELF_LOOKUP,
  // SEQUENCE, SUFFIX_WHEN, MAP_BOOLEAN, CONDITIONAL_DATE), so an analyst opening a
  // supplier field hit `RULE_SPECS[type]` === undefined and the very next line
  // dereferenced it — the modal threw, React unmounted the tree, and the analyst
  // saw an empty dialog. That is CW #6: "the saved transformation does not appear",
  // for a rule that saves correctly and reaches the output file.
  //
  // Each gets a real entry and edits through the raw JSON editor. A form we have
  // not written is a reason to show the config, not to lose it.
  MAP_BOOLEAN: { label: "Map boolean (Y/N)", description: "Normalise yes/no/true/false spellings", defaultConfig: () => ({}), Form: RawConfigOnly },
  CONDITIONAL_DATE: { label: "Conditional date", description: "Pick a date column by condition", defaultConfig: () => ({}), rowAware: true, Form: RawConfigOnly },
  PHONE_PART: { label: "Phone part", description: "Country / area / number / extension from one phone string", defaultConfig: () => ({ part: "number" }), Form: RawConfigOnly },
  PREFIX: { label: "Prefix", description: "Prepend a fixed string", defaultConfig: () => ({ value: "" }), Form: RawConfigOnly },
  SUFFIX: { label: "Suffix", description: "Append a fixed string", defaultConfig: () => ({ value: "" }), Form: RawConfigOnly },
  SUFFIX_WHEN: { label: "Suffix when", description: "Append a string only when a condition holds", defaultConfig: () => ({}), rowAware: true, Form: RawConfigOnly },
  SEQUENCE: { label: "Running sequence", description: "Unique running key with an optional variant form", defaultConfig: () => ({ prefix: "", width: 6, start: 1 }), needsSourceColumn: false, Form: RawConfigOnly },
  SELF_LOOKUP: { label: "Self lookup", description: "Resolve a value from another row of the same file", defaultConfig: () => ({}), rowAware: true, needsSourceColumn: false, Form: RawConfigOnly },
  CITY_COUNTRY_KEY: { label: "Country-city key", description: "Join an ISO country code and a city into one key", defaultConfig: () => ({}), rowAware: true, needsSourceColumn: false, Form: RawConfigOnly },
  BLANK_IF_EQUALS: { label: "Blank if equals", description: "Clear the value when it duplicates another column", defaultConfig: () => ({ other_column: "" }), rowAware: true, Form: RawConfigOnly },
};

/** Fallback editor for rule types with no bespoke form. */
function RawConfigOnly({ config }: { config: any }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
      <div className="mb-1 font-medium text-slate-700">No visual editor for this rule type yet.</div>
      <div>Its settings are shown and editable under <span className="font-medium">Advanced (raw JSON)</span> below. The rule itself works normally.</div>
      <pre className="mt-2 max-h-40 overflow-auto rounded bg-white p-2 text-[11px] text-slate-700">{JSON.stringify(config ?? {}, null, 2)}</pre>
    </div>
  );
}

/** Never let an unknown rule type take the dialog down. */
const UNKNOWN_SPEC: RuleSpec = {
  label: "Saved rule",
  description: "This rule type has no editor in this build; its configuration is shown as raw JSON.",
  defaultConfig: () => ({}),
  needsSourceColumn: false,
  Form: RawConfigOnly,
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
  // Conversion ids are ObjectId hex strings; number is tolerated for older callers.
  conversionId: string | number;
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
  // FBDI field ids are ObjectId hex strings, not numbers — keep them as-is.
  const [targetFieldId, setTargetFieldId] = useState<string | number | null>(
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

  // Natural-language translator state. The local pattern matcher
  // works without an API key for most rules — we never auto-hide the
  // pane anymore. ``nlSource`` records whether the latest translation
  // came from the deterministic matcher or from Claude.
  const [nlOpen, setNlOpen] = useState(false);
  const [nlDescription, setNlDescription] = useState("");
  const [nlBusy, setNlBusy] = useState(false);
  const [nlExplanation, setNlExplanation] = useState<string | null>(null);
  const [nlAmbiguities, setNlAmbiguities] = useState<
    { phrase: string; interpreted_as: string; alternatives: string[] }[]
  >([]);
  const [nlError, setNlError] = useState<string | null>(null);
  const [nlSource, setNlSource] = useState<"local" | "ai" | null>(null);

  // Existing saved rules for this conversion/target field. A rule the analyst
  // already saved must come BACK into this form when they reopen it — otherwise
  // the modal looks empty even though the rule is live in the output, and
  // pressing Save silently stacks a duplicate.
  const [existingRules, setExistingRules] = useState<TransformationRule[]>([]);
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [loadingExisting, setLoadingExisting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const applyRuleToForm = (r: TransformationRule) => {
    const cfg = r.rule_config || {};
    setEditingRuleId(String(r.id));
    setType(r.rule_type);
    setConfig(cfg);
    setAdvancedRaw(JSON.stringify(cfg, null, 2));
    setSourceColumn(r.source_column || "");
    setDescription(r.description || "");
    if (r.target_field_id != null) setTargetFieldId(String(r.target_field_id));
  };

  const startNewRule = () => {
    setEditingRuleId(null);
    setType("VALUE_MAP");
    const cfg = RULE_SPECS.VALUE_MAP.defaultConfig();
    setConfig(cfg);
    setAdvancedRaw(JSON.stringify(cfg, null, 2));
    setSourceColumn(defaultSourceColumn ?? "");
    setDescription("");
  };

  // Reset on open, then load any rule already saved for this target field.
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
    setNlOpen(false);
    setNlDescription("");
    setNlExplanation(null);
    setNlAmbiguities([]);
    setNlError(null);
    setNlSource(null);
    setEditingRuleId(null);
    setExistingRules([]);

    let cancelled = false;
    (async () => {
      setLoadingExisting(true);
      setLoadError(null);
      try {
        const all = await MappingApi.rules(String(conversionId));
        if (cancelled) return;
        setExistingRules(all);
        // Pre-load the rule saved for THIS field (latest by sequence wins).
        if (defaultTargetFieldId != null) {
          const mine = all
            .filter((r) => String(r.target_field_id ?? "") === String(defaultTargetFieldId))
            .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
          const last = mine[mine.length - 1];
          if (last) applyRuleToForm(last);
        }
      } catch (e: any) {
        // NOT best-effort, silently. A failed list is indistinguishable from "no
        // rule saved" if it says nothing, and that is exactly how the earlier
        // ObjectId 500 presented to the analyst — as a missing rule. Authoring a new
        // rule still works, so this warns rather than blocking.
        if (!cancelled) {
          setLoadError(
            e?.response?.data?.detail ||
            "Could not load the rules already saved for this conversion — this dialog may be showing a blank form for a field that already has one.",
          );
        }
      } finally {
        if (!cancelled) setLoadingExisting(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, conversionId, defaultTargetFieldId]);

  // Rules already saved against the currently-selected target field.
  const rulesForField = useMemo(
    () =>
      targetFieldId == null
        ? []
        : existingRules
            .filter((r) => String(r.target_field_id ?? "") === String(targetFieldId))
            .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0)),
    [existingRules, targetFieldId],
  );

  const translateNL = async () => {
    if (!nlDescription.trim()) return;
    setNlBusy(true);
    setNlError(null);
    try {
      const res = await MappingApi.translateRule(String(conversionId), {
        description: nlDescription,
        target_field_id: targetFieldId != null ? String(targetFieldId) : undefined,
        source_column: sourceColumn || undefined,
        sample_size: 5,
      });
      // Mirror the translated rule into the structured form. The user
      // can confirm or edit before saving.
      setType(res.rule_type);
      setConfig(res.config || {});
      setAdvancedRaw(JSON.stringify(res.config || {}, null, 2));
      setNlExplanation(res.explanation || null);
      setNlAmbiguities(res.ambiguities || []);
      setNlSource(res.source || "ai");
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Translation failed";
      setNlError(detail);
    } finally {
      setNlBusy(false);
    }
  };

  // Default config swap on type change
  const onTypeChange = (next: string) => {
    setType(next);
    const cfg = (RULE_SPECS[next] || UNKNOWN_SPEC).defaultConfig();
    setConfig(cfg);
    setAdvancedRaw(JSON.stringify(cfg, null, 2));
  };

  // Belt and braces. Even with every backend type registered above, a rule saved by
  // a NEWER build than this bundle would otherwise crash the dialog — and the
  // failure mode is silent (React unmounts, no error), which is what made CW #6 read
  // as "the rule does not appear" rather than "the page broke".
  const spec = RULE_SPECS[type] || UNKNOWN_SPEC;

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
        const res = await MappingApi.previewRules(String(conversionId), {
          source_column: sourceColumn || undefined,
          rules: [{ rule_type: type, config: activeCfg }],
          sample_size: 5,
        });
        setPreview(res.samples);
        setPreviewError(null);
      } catch (e: any) {
        // A bare axios "Network Error" means no response came back (backend
        // restarting / cold start). Say that plainly — the rule is still saveable.
        const detail = e?.response?.data?.detail;
        const isNetwork = !e?.response && /network|failed to fetch/i.test(e?.message || "");
        setPreviewError(
          detail ||
          (isNetwork
            ? "Couldn't reach the server for a preview — it may be waking up. You can still save the rule."
            : e?.message) ||
          "Preview failed"
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
      const body = {
        target_field_id: targetFieldId != null ? String(targetFieldId) : undefined,
        source_column: sourceColumn || undefined,
        rule_type: type,
        rule_config: activeCfg,
        description: description || undefined,
      };
      // Editing a rule that already exists updates it in place; only a genuinely
      // new rule is inserted. Previously every save inserted, so reopening and
      // saving stacked duplicate rules on the same field.
      if (editingRuleId) await MappingApi.updateRule(editingRuleId, body);
      else await MappingApi.addRule(String(conversionId), body);
      onSaved();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  const removeRule = async (id: string) => {
    try {
      await MappingApi.deleteRule(id);
      setExistingRules((rs) => rs.filter((r) => String(r.id) !== id));
      if (editingRuleId === id) startNewRule();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Failed to delete rule");
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editingRuleId ? "Edit transformation rule" : "Add transformation rule"}
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
              <Check className="h-3.5 w-3.5" />
              {editingRuleId ? "Update & learn" : "Save & learn"}
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid grid-cols-[1fr_320px] gap-5">
        {/* Left: form */}
        <div className="space-y-4">
          {/* Natural-language translator — only when the server reports the
              translator is reachable. Collapsed by default so the structured
              form remains the primary affordance. */}
          {true && (
            <div className="rounded-md border border-brand/30 bg-brand-subtle/15">
              <button
                onClick={() => setNlOpen((o) => !o)}
                className="flex w-full items-center justify-between px-3 py-2 text-left"
              >
                <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-brand-dark">
                  <MessageSquare className="h-3.5 w-3.5" />
                  Describe this rule in plain English
                </span>
                {nlOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 text-brand-dark" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 text-brand-dark" />
                )}
              </button>
              {nlOpen && (
                <div className="border-t border-brand/30 p-3">
                  <textarea
                    className="input min-h-[88px] text-[12.5px]"
                    placeholder="e.g. if STATUS is active and REGION is US then DOMESTIC_ACTIVE; if STATUS is active and REGION is anything else then INTERNATIONAL_ACTIVE; otherwise INACTIVE"
                    value={nlDescription}
                    onChange={(e) => setNlDescription(e.target.value)}
                  />
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <div className="text-[10.5px] text-ink-muted">
                      The translator picks the rule type for you and pre-fills
                      the form below. Review the structured form, then save.
                    </div>
                    <Button
                      onClick={translateNL}
                      loading={nlBusy}
                      disabled={!nlDescription.trim()}
                      className="!h-8"
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      Translate
                    </Button>
                  </div>
                  {nlError && (
                    <div className="mt-2 rounded-md bg-danger-subtle px-3 py-2 text-[11px] text-danger">
                      <AlertTriangle className="mr-1 inline h-3 w-3" />
                      {nlError}
                    </div>
                  )}
                  {nlExplanation && (
                    <div className="mt-2 rounded-md border border-brand/30 bg-white px-3 py-2 text-[11.5px] text-ink">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-brand-dark">
                          Interpretation
                        </span>
                        {nlSource && (
                          <span className={cn(
                            "rounded-full px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-wider",
                            nlSource === "local"
                              ? "bg-success-subtle text-success"
                              : "bg-brand-subtle text-brand-dark"
                          )}>
                            {nlSource === "local" ? "Local · deterministic" : "AI · Claude"}
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 leading-snug">{nlExplanation}</div>
                      <div className="mt-1.5 text-[10px] text-ink-muted">
                        Rule populated in the structured form below — review, edit, then Save.
                      </div>
                    </div>
                  )}
                  {nlAmbiguities.length > 0 && (
                    <div className="mt-2 rounded-md border border-warning/40 bg-warning-subtle/50 px-3 py-2">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-warning">
                        Confirm interpretation
                      </div>
                      <ul className="mt-1 space-y-1 text-[11.5px] text-ink">
                        {nlAmbiguities.map((a, i) => (
                          <li key={i}>
                            <span className="font-mono">“{a.phrase}”</span> →{" "}
                            <span className="font-medium">{a.interpreted_as}</span>
                            {a.alternatives.length > 0 && (
                              <span className="text-ink-muted">
                                {" "}(alternatives: {a.alternatives.join(", ")})
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Rules already saved for this field — loaded back so the analyst sees
              what is live, can edit it, or add another on top. */}
          {loadError ? (
            <div className="rounded-md border border-warning/50 bg-warning-subtle/40 px-3 py-2 text-[11px] text-warning">
              {loadError}
            </div>
          ) : null}
          {loadingExisting ? (
            <div className="rounded-md border border-line bg-canvas px-3 py-2 text-[11px] text-ink-muted">
              Checking for rules already saved on this field…
            </div>
          ) : rulesForField.length > 0 ? (
            <div className="rounded-md border border-success/40 bg-success-subtle/40">
              <div className="flex items-center justify-between px-3 py-2">
                <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-success">
                  <Check className="h-3.5 w-3.5" />
                  {rulesForField.length} rule{rulesForField.length !== 1 ? "s" : ""} already saved
                  on this field
                </span>
                {editingRuleId && (
                  <button
                    onClick={startNewRule}
                    className="text-[11px] font-medium text-brand-dark hover:underline"
                  >
                    + Add another instead
                  </button>
                )}
              </div>
              <div className="border-t border-success/30 px-3 py-2 space-y-1">
                {rulesForField.map((r) => {
                  const active = editingRuleId === String(r.id);
                  return (
                    <div
                      key={String(r.id)}
                      className={cn(
                        "flex items-center justify-between gap-2 rounded px-2 py-1",
                        active ? "bg-white ring-1 ring-success/50" : "hover:bg-white/60",
                      )}
                    >
                      <button
                        onClick={() => applyRuleToForm(r)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <span className="rounded bg-brand-subtle px-1.5 py-0.5 font-mono text-[10px] text-brand-dark">
                          {RULE_SPECS[r.rule_type]?.label ?? r.rule_type}
                        </span>
                        <span className="truncate font-mono text-[10.5px] text-ink-muted">
                          {r.source_column || "(row-aware)"}
                        </span>
                        {active && (
                          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-success">
                            editing
                          </span>
                        )}
                      </button>
                      <button
                        onClick={() => removeRule(String(r.id))}
                        title="Delete this saved rule"
                        className="shrink-0 rounded p-1 text-ink-subtle hover:bg-white hover:text-danger"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

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
                onChange={(e) => setTargetFieldId(e.target.value || null)}
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
