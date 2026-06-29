import React, { useEffect, useState } from "react";
import { LearningApi } from "@/api";
import { Button, Modal } from "@/components/ui/Primitives";
import type { LearnedMapping } from "@/types";

const CATEGORIES = [
  "Column Mapping Alias", "Status Value Mapping", "UOM Conversion Rule",
  "Currency Mapping", "Organization Code Mapping", "Branch Code Mapping",
  "SKU / Item Format Alias", "Customer Alias", "Supplier Alias", "Date Format Rule",
];
const RULE_TYPES = [
  "VALUE_MAP", "REMOVE_HYPHEN", "DEFAULT_VALUE", "CONCAT", "UPPERCASE",
  "TRIM", "PAD_LEFT", "DATE_FORMAT", "Custom Rule",
];

type Props = {
  open: boolean;
  kind: "rule" | "crosswalk";
  initial?: LearnedMapping | null;
  onClose: () => void;
  onSaved: () => void;
};

/** Shared create/edit form for Rule Library and Crosswalk Library entries. */
export const LearnedEntryModal: React.FC<Props> = ({ open, kind, initial, onClose, onSaved }) => {
  const isRule = kind === "rule";
  const [category, setCategory] = useState("");
  const [ruleType, setRuleType] = useState("");
  const [original, setOriginal] = useState("");
  const [resolved, setResolved] = useState("");
  const [object, setObject] = useState("");
  const [field, setField] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCategory(initial?.category || (isRule ? "Column Mapping Alias" : "Status Value Mapping"));
    setRuleType(initial?.rule_type || "");
    setOriginal(initial?.original_value || "");
    setResolved(initial?.resolved_value || "");
    setObject(initial?.target_object || "");
    setField(initial?.target_field || "");
    setErr(null);
  }, [open, initial, isRule]);

  const save = async () => {
    if (!category.trim() || !original.trim() || !resolved.trim()) {
      setErr("Category, original and resolved values are required.");
      return;
    }
    setSaving(true);
    setErr(null);
    const body: Partial<LearnedMapping> = {
      kind, category: category.trim(),
      original_value: original.trim(), resolved_value: resolved.trim(),
      target_object: object.trim() || undefined,
      target_field: field.trim() || undefined,
      rule_type: isRule ? (ruleType.trim() || undefined) : undefined,
    };
    try {
      if (initial?.id) await LearningApi.update(initial.id, body);
      else await LearningApi.capture(body);
      onSaved();
      onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Could not save. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const L = isRule
    ? { title: initial ? "Edit rule" : "Add rule", orig: "Original (column / value)", res: "Resolved (result)" }
    : { title: initial ? "Edit crosswalk" : "Add crosswalk", orig: "Original (legacy value)", res: "Resolved (Fusion value)" };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={L.title}
      size="md"
      footer={
        <div className="flex w-full items-center justify-between">
          <span className="text-[11px] text-danger">{err || ""}</span>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={save} loading={saving} disabled={!category || !original || !resolved}>
              {initial ? "Save changes" : "Add"}
            </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Category</label>
            <input className="input" list="lem-cats" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Status Value Mapping" />
            <datalist id="lem-cats">{CATEGORIES.map((c) => <option key={c} value={c} />)}</datalist>
          </div>
          {isRule && (
            <div>
              <label className="label">Rule type</label>
              <input className="input" list="lem-types" value={ruleType} onChange={(e) => setRuleType(e.target.value)} placeholder="e.g. VALUE_MAP" />
              <datalist id="lem-types">{RULE_TYPES.map((t) => <option key={t} value={t} />)}</datalist>
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">{L.orig}</label>
            <input className="input" value={original} onChange={(e) => setOriginal(e.target.value)} placeholder="e.g. A" />
          </div>
          <div>
            <label className="label">{L.res}</label>
            <input className="input" value={resolved} onChange={(e) => setResolved(e.target.value)} placeholder="e.g. Active" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Target object <span className="text-ink-subtle">(optional)</span></label>
            <input className="input" value={object} onChange={(e) => setObject(e.target.value)} placeholder="e.g. Unit of Measure" />
          </div>
          <div>
            <label className="label">Target field <span className="text-ink-subtle">(optional)</span></label>
            <input className="input" value={field} onChange={(e) => setField(e.target.value)} placeholder="e.g. UOMCode" />
          </div>
        </div>
      </div>
    </Modal>
  );
};
