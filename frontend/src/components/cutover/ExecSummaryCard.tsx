import React, { useEffect, useState } from "react";
import {
  TrendingUp, AlertCircle, AlertOctagon, Calendar, ChevronRight,
  Loader2, RefreshCw, DollarSign, ArrowUpRight,
} from "lucide-react";
import { Slice6Api } from "@/api";
import { Card, CardBody, CardHeader, Pill } from "@/components/ui/Primitives";
import { cn, formatDate } from "@/lib/utils";
import type { ExecSummary, ReadinessScore } from "@/types";

/**
 * Exec summary card — embedded at the top of Project Overview.
 *
 * A CFO / steering-committee view that rolls up:
 *
 *   • Migration Readiness Score (0–100 with per-lens breakdown)
 *   • Safeguard pass rate, days-to-cutover, critical issues count
 *   • Top 5 risks (by probability × impact) and top 5 blockers
 *   • Total reconciliation variance ($)
 *   • Discovery integration-degradation count
 *
 * Auto-refreshes on demand. Hides itself cleanly when the project is too
 * fresh to have any of these signals (handled at the empty-state level
 * by the panel's empty branches).
 */

export const ExecSummaryCard: React.FC<{ projectId: number }> = ({ projectId }) => {
  const [summary, setSummary] = useState<ExecSummary | null>(null);
  const [readiness, setReadiness] = useState<ReadinessScore | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = async () => {
    setBusy(true);
    try {
      const [s, r] = await Promise.all([
        Slice6Api.execSummary(projectId),
        Slice6Api.readiness(projectId),
      ]);
      setSummary(s);
      setReadiness(r);
    } finally {
      setBusy(false);
    }
  };
  const recomputeDQ = async () => {
    setBusy(true);
    try {
      await Slice6Api.recomputeProjectQualityScores(projectId);
      await reload();
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { reload(); }, [projectId]);

  if (!summary || !readiness) {
    return (
      <Card className="mt-4">
        <CardHeader title="Migration Readiness" subtitle="Loading…" />
        <CardBody>
          <Loader2 className="h-4 w-4 animate-spin text-ink-muted" />
        </CardBody>
      </Card>
    );
  }

  const score = summary.score_pct;
  const scoreTone = score >= 80 ? "text-success" : score >= 60 ? "text-warning" : "text-danger";

  return (
    <Card className="mt-4 border-2 border-brand/30 bg-gradient-to-br from-brand-subtle/40 to-white">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            <TrendingUp className="h-4 w-4 text-brand" />
            Migration Readiness · Exec Summary
          </span>
        }
        subtitle="CFO / steering committee rollup — single signal for go / no-go"
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={recomputeDQ}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium text-ink-muted hover:border-brand hover:text-brand-dark disabled:opacity-60"
              title="Recompute Data Quality scores across all conversions"
            >
              <RefreshCw className={cn("h-3 w-3", busy && "animate-spin")} /> DQ scores
            </button>
            <button
              onClick={reload}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium text-ink-muted hover:border-brand hover:text-brand-dark disabled:opacity-60"
            >
              <RefreshCw className={cn("h-3 w-3", busy && "animate-spin")} /> Refresh
            </button>
          </div>
        }
      />
      <CardBody>
        {/* Top row: score + 4 KPI tiles */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <ScoreTile score={score} score5={summary.score_5} tone={scoreTone} />
          <KpiTile
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            label="Gates passing"
            value={`${Math.round(summary.safeguard_pass_rate * 7)}/7`}
            tone="text-info"
          />
          <KpiTile
            icon={<Calendar className="h-3.5 w-3.5" />}
            label="Days to cutover"
            value={summary.days_to_cutover != null ? String(summary.days_to_cutover) : "—"}
            tone={
              summary.days_to_cutover != null && summary.days_to_cutover < 14
                ? "text-danger"
                : "text-ink"
            }
          />
          <KpiTile
            icon={<AlertCircle className="h-3.5 w-3.5" />}
            label="Critical issues"
            value={String(summary.open_critical_issues)}
            tone={summary.open_critical_issues > 0 ? "text-danger" : "text-success"}
          />
          <KpiTile
            icon={<DollarSign className="h-3.5 w-3.5" />}
            label="|Recon variance|"
            value={`$${Math.round(summary.total_recon_variance_usd).toLocaleString()}`}
            tone={summary.total_recon_variance_usd > 10_000 ? "text-warning" : "text-ink"}
          />
        </div>

        {/* Lens breakdown */}
        <div className="mt-4">
          <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Score breakdown
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
            {Object.entries(readiness.lenses).map(([code, lens]) => (
              <LensTile key={code} code={code} lens={lens} />
            ))}
          </div>
        </div>

        {/* Top risks + blockers — side by side */}
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <div className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              Top risks (probability × impact)
            </div>
            {summary.top_risks.length === 0 ? (
              <div className="rounded-md border border-line bg-canvas px-3 py-3 text-[11.5px] text-ink-muted">
                No open risks logged.
              </div>
            ) : (
              <div className="space-y-1">
                {summary.top_risks.map((r) => (
                  <div key={r.id} className="flex items-center justify-between rounded-md border border-line bg-white px-3 py-1.5">
                    <span className="text-[12px] font-medium text-ink">{r.title}</span>
                    <Pill tone={r.score >= 15 ? "danger" : r.score >= 9 ? "warning" : "neutral"} className="!text-[10px]">
                      {r.score} (P{r.probability}×I{r.impact})
                    </Pill>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              Top open blockers
            </div>
            {summary.top_blockers.length === 0 ? (
              <div className="rounded-md border border-line bg-canvas px-3 py-3 text-[11.5px] text-ink-muted">
                No open blockers.
              </div>
            ) : (
              <div className="space-y-1">
                {summary.top_blockers.map((i) => (
                  <div key={i.id} className="flex items-center justify-between rounded-md border border-line bg-white px-3 py-1.5">
                    <span className="text-[12px] font-medium text-ink">{i.title}</span>
                    <Pill tone={
                      i.severity === "critical" || i.severity === "high" ? "danger" :
                      i.severity === "medium" ? "warning" : "neutral"
                    } className="!text-[10px]">
                      {i.severity}
                    </Pill>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Discovery hint */}
        {summary.pillar_complexity != null && (
          <div className="mt-4 flex items-center justify-between rounded-md bg-canvas px-3 py-2 text-[11.5px] text-ink-muted">
            <span>
              Discovery complexity:{" "}
              <span className="font-semibold text-ink">{Math.round(summary.pillar_complexity)}/100</span>
              {summary.integrations_degraded > 0 && (
                <>
                  {" · "}
                  <span className="text-warning">
                    {summary.integrations_degraded} integration{summary.integrations_degraded === 1 ? "" : "s"} degraded
                  </span>
                </>
              )}
            </span>
          </div>
        )}
      </CardBody>
    </Card>
  );
};

const ScoreTile: React.FC<{ score: number; score5: number; tone: string }> = ({
  score, score5, tone,
}) => (
  <div className="rounded-md border-2 border-line bg-white p-3">
    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
      Readiness
    </div>
    <div className="mt-1 flex items-baseline gap-2">
      <span className={cn("text-3xl font-bold tabular-nums", tone)}>{score}</span>
      <span className="text-[11px] text-ink-muted">/100</span>
    </div>
    <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">{score5.toFixed(1)} / 5.0</div>
  </div>
);

const KpiTile: React.FC<{
  icon: React.ReactNode; label: string; value: string; tone: string;
}> = ({ icon, label, value, tone }) => (
  <div className="rounded-md border border-line bg-white p-3">
    <div className="flex items-center gap-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
      {icon}{label}
    </div>
    <div className={cn("mt-1 text-xl font-semibold tabular-nums", tone)}>{value}</div>
  </div>
);

const LensTile: React.FC<{
  code: string;
  lens: { label: string; value_pct: number; weight: number };
}> = ({ lens }) => {
  const tone = lens.value_pct >= 80 ? "bg-success" : lens.value_pct >= 50 ? "bg-warning" : "bg-danger";
  return (
    <div className="rounded-md border border-line bg-white px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-wider text-ink-muted">{lens.label}</div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="font-mono text-base font-semibold tabular-nums text-ink">{lens.value_pct}</span>
        <span className="text-[10.5px] text-ink-muted">/100</span>
        <span className="ml-auto font-mono text-[9.5px] text-ink-muted">×{lens.weight.toFixed(2)}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
        <div className={cn("h-full rounded-full transition-all", tone)} style={{ width: `${lens.value_pct}%` }} />
      </div>
    </div>
  );
};
