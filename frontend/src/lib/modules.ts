// Module taxonomy — the one place that decides which Fusion module a dataset or
// engagement belongs to, so the Datasets and Projects pages can group cards under
// the same headings. Inference is keyword-based over whatever text a card carries
// (name, file name, the conversions attached to it): there is no stored "module"
// column on a dataset, and an engagement's is often only implied by its name.
//
// Order below is BOTH the match order and the section order. First match wins, so
// the less ambiguous modules come first (an "eBOS Supplier" file must read as
// Supplier before "…BOM…" logic could ever see it). "Other" is the implicit
// fallback and always renders last.
import {
  Users, Truck, UserCog, Boxes, Package, ClipboardList, BookOpen, Layers,
  type LucideIcon,
} from "lucide-react";

export interface ModuleDef {
  key: string;
  label: string;   // short chip label, e.g. "Supplier"
  full: string;    // section heading, e.g. "Supplier / Procurement"
  icon: LucideIcon;
  accent: string;  // tailwind text colour for the heading icon
  test: RegExp;
}

export const MODULES: ModuleDef[] = [
  { key: "hcm",      label: "Employee",     full: "Employee / HCM",         icon: UserCog,       accent: "text-violet-600",  test: /employ|worker|\bhcm\b|\bhdl\b|demographic|\bperson\b|assignment|position|payroll|workday/i },
  { key: "supplier", label: "Supplier",     full: "Supplier / Procurement", icon: Truck,         accent: "text-amber-600",   test: /supplier|vendor|payable|\bap\b|\bpoz\b|procure|purchase|\bpo\b|e-?bos/i },
  { key: "bom",      label: "BOM",          full: "Bill of Materials",      icon: Boxes,         accent: "text-teal-600",    test: /\bbom\b|bill of material|structure|component|egp/i },
  { key: "customer", label: "Customer",     full: "Customer / Receivables", icon: Users,         accent: "text-indigo-600",  test: /customer|\bparty\b|receivable|\bar\b|hz_|contact/i },
  { key: "item",     label: "Items",        full: "Items / Inventory",      icon: Package,       accent: "text-sky-600",     test: /\bitem|inventory|\bsku\b|product|mtl_system/i },
  { key: "order",    label: "Sales Orders", full: "Sales Orders",           icon: ClipboardList, accent: "text-rose-600",    test: /sales.?order|order.?management|\boe_|order extract/i },
  { key: "gl",       label: "GL",           full: "General Ledger",         icon: BookOpen,      accent: "text-emerald-600", test: /ledger|journal|\bgl\b|chart of account|\bcoa\b|financial/i },
];

export const OTHER_MODULE: ModuleDef = {
  key: "other", label: "Other", full: "Other", icon: Layers, accent: "text-slate-500", test: /(?!)/,
};

/** Infer the module from any number of text fragments (name, file, conversions…). */
export function moduleFor(...parts: (string | null | undefined)[]): ModuleDef {
  const hay = parts.filter(Boolean).join("  ");
  for (const m of MODULES) if (m.test.test(hay)) return m;
  return OTHER_MODULE;
}

/**
 * Bucket items by inferred module, preserving MODULES order with "Other" last and
 * dropping empty modules. `textOf` returns the fragments to infer from.
 */
export function groupByModule<T>(
  items: T[],
  textOf: (t: T) => (string | null | undefined)[],
): { module: ModuleDef; items: T[] }[] {
  const buckets = new Map<string, T[]>();
  for (const it of items) {
    const m = moduleFor(...textOf(it));
    (buckets.get(m.key) ?? buckets.set(m.key, []).get(m.key)!).push(it);
  }
  return [...MODULES, OTHER_MODULE]
    .filter((m) => buckets.has(m.key))
    .map((m) => ({ module: m, items: buckets.get(m.key)! }));
}
