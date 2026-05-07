import React from "react";
import { Loader2, X } from "lucide-react";
import { cn, confidenceTone } from "@/lib/utils";

// ---------------- Card ----------------
export const Card: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className, ...rest }) => (
  <div className={cn("card", className)} {...rest} />
);

export const CardHeader: React.FC<{
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}> = ({ title, subtitle, actions, className }) => (
  <div className={cn("card-header", className)}>
    <div>
      <div className="text-sm font-semibold text-ink">{title}</div>
      {subtitle && <div className="mt-0.5 text-xs text-ink-muted">{subtitle}</div>}
    </div>
    {actions && <div className="flex items-center gap-2">{actions}</div>}
  </div>
);

export const CardBody: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className, ...rest }) => (
  <div className={cn("p-5", className)} {...rest} />
);

// ---------------- Pill (status / severity) ----------------
type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "brand";
const TONE_BG: Record<Tone, string> = {
  success: "bg-success-subtle text-success",
  warning: "bg-warning-subtle text-warning",
  danger: "bg-danger-subtle text-danger",
  info: "bg-info-subtle text-info",
  neutral: "bg-canvas text-ink-muted border border-line",
  brand: "bg-brand-subtle text-brand-dark",
};

export const Pill: React.FC<{ tone?: Tone; children: React.ReactNode; className?: string }> = ({
  tone = "neutral",
  children,
  className,
}) => <span className={cn("pill", TONE_BG[tone], className)}>{children}</span>;

// ---------------- Buttons ----------------
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
};
export const Button: React.FC<BtnProps> = ({ variant = "primary", loading, className, children, disabled, ...rest }) => {
  const cls = {
    primary: "btn-primary",
    secondary: "btn-secondary",
    ghost: "btn-ghost",
    danger: "btn-danger",
  }[variant];
  return (
    <button className={cn(cls, className)} disabled={disabled || loading} {...rest}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
};

// ---------------- ConfidenceBar (used purposefully — color encodes meaning) ----------------
export const ConfidenceBar: React.FC<{ value: number; className?: string }> = ({ value, className }) => {
  const tone = confidenceTone(value);
  const pct = Math.round(value * 100);
  const bar = { success: "bg-success", warning: "bg-warning", danger: "bg-danger" }[tone];
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-line">
        <div className={cn("h-full rounded-full", bar)} style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
      <span className="font-mono text-xs text-ink-muted tabular-nums">{pct}%</span>
    </div>
  );
};

// ---------------- Spinner / Empty / Section ----------------
export const Spinner: React.FC<{ className?: string }> = ({ className }) => (
  <Loader2 className={cn("h-4 w-4 animate-spin text-ink-muted", className)} />
);

export const PageLoader: React.FC<{ label?: string }> = ({ label = "Loading…" }) => (
  <div className="flex h-full min-h-[200px] items-center justify-center gap-2 text-ink-muted">
    <Loader2 className="h-4 w-4 animate-spin" /> <span className="text-sm">{label}</span>
  </div>
);

export const EmptyState: React.FC<{
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}> = ({ title, description, icon, action }) => (
  <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line bg-white px-6 py-12 text-center">
    {icon && <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">{icon}</div>}
    <div className="text-sm font-semibold text-ink">{title}</div>
    {description && <div className="mt-1 max-w-md text-xs text-ink-muted">{description}</div>}
    {action && <div className="mt-4">{action}</div>}
  </div>
);

export const PageTitle: React.FC<{ title: string; subtitle?: React.ReactNode; right?: React.ReactNode }> = ({ title, subtitle, right }) => (
  <div className="mb-5 flex items-end justify-between">
    <div>
      <h1 className="text-2xl font-semibold text-ink">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>}
    </div>
    {right && <div className="flex items-center gap-2">{right}</div>}
  </div>
);

// ---------------- Modal ----------------
export const Modal: React.FC<{
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}> = ({ open, onClose, title, children, footer, size = "md" }) => {
  if (!open) return null;
  const w = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-5xl" }[size];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4 backdrop-blur-sm" onClick={onClose}>
      <div className={cn("w-full rounded-xl bg-white shadow-soft", w)} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="text-sm font-semibold text-ink">{title}</div>
          <button onClick={onClose} className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink"><X className="h-4 w-4" /></button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto p-5">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line bg-canvas px-5 py-3 rounded-b-xl">{footer}</div>}
      </div>
    </div>
  );
};

// ---------------- Tabs (simple) ----------------
export const Tabs: React.FC<{
  value: string;
  onChange: (v: string) => void;
  items: { value: string; label: React.ReactNode; count?: number }[];
}> = ({ value, onChange, items }) => (
  <div className="border-b border-line">
    <nav className="-mb-px flex gap-1">
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            onClick={() => onChange(it.value)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium transition",
              active
                ? "border-brand text-brand-dark"
                : "border-transparent text-ink-muted hover:border-line hover:text-ink"
            )}
          >
            {it.label}
            {typeof it.count === "number" && (
              <span className={cn("ml-2 rounded-full px-1.5 py-0.5 text-[10px]",
                active ? "bg-brand-subtle text-brand-dark" : "bg-canvas text-ink-muted")}>{it.count}</span>
            )}
          </button>
        );
      })}
    </nav>
  </div>
);
