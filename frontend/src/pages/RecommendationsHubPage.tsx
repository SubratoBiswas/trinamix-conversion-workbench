import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles, Wand2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, MappingApi, LearningApi, ProjectsApi } from "@/api";
import {
  Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill, Spinner,
} from "@/components/ui/Primitives";
import { RecommendationCard } from "@/components/recommendations/RecommendationCard";
import { RuleAuthorModal } from "@/components/transforms/RuleAuthorModal";
import { buildRecommendations, type Recommendation } from "@/lib/recommendations";
import type {
  Conversion,
  DatasetDetail,
  FBDIField,
  Project,
} from "@/types";

interface ProjectRecs {
  project: Conversion;
  dataset: DatasetDetail;
  fields: FBDIField[];
  recs: Recommendation[];
}

/**
 * Per-engagement recommendations hub. Pick a project from the dropdown; each
 * conversion in it loads its recommendations independently (lazy, in parallel)
 * so the page renders immediately — important for EBS conversions whose source
 * columns are fetched live and would otherwise block the whole page.
 */
export const RecommendationsHubPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [convs, setConvs] = useState<Conversion[] | null>(null);
  const [authoring, setAuthoring] = useState<ProjectRecs | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const [learned, setLearned] = useState<Set<string>>(new Set());

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2400); };

  const handleApply = async (entry: ProjectRecs, rec: Recommendation, learn: boolean) => {
    try {
      await MappingApi.addRule(entry.project.id, {
        source_column: rec.column,
        rule_type: rec.ruleType || rec.kind,
        rule_config: rec.config || {},
        description: rec.title,
      });
      setApplied((prev) => new Set(prev).add(rec.id));
      if (learn) {
        try {
          await LearningApi.capture({
            source_erp: entry.project.source_system || "unknown",
            source_column: rec.column,
            target_field_name: rec.targetField || rec.column,
            rule_type: rec.ruleType || rec.kind,
            rule_config: rec.config || {},
            confidence: rec.confidence,
            note: rec.reason || "",
          } as any);
          setLearned((prev) => new Set(prev).add(rec.id));
        } catch { /* learn failure is non-fatal */ }
      }
      flash(learn ? "Rule applied & learned" : "Rule applied");
    } catch {
      flash("Failed to apply rule");
    }
  };

  // Engagement list — pick an initial project (URL ?project= wins).
  useEffect(() => {
    ProjectsApi.list().then((ps) => {
      setProjects(ps);
      const qs = new URLSearchParams(window.location.search).get("project");
      setProjectId(qs || (ps[0] ? String(ps[0].id) : null));
    }).catch(() => setProjects([]));
  }, []);

  // Conversions for the selected engagement (fast — recs load per card).
  useEffect(() => {
    if (!projectId) { setConvs([]); return; }
    setConvs(null);
    ProjectsApi.conversions(String(projectId)).then(setConvs).catch(() => setConvs([]));
  }, [projectId]);

  const selectedProject = projects.find((p) => String(p.id) === String(projectId));
  const templated = (convs || []).filter((c) => c.template_id);

  return (
    <>
      <PageTitle
        title="Recommendations"
        subtitle="AI suggestions tied to source data + FBDI target metadata, per engagement"
        right={
          <div>
            <label className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
              Engagement
            </label>
            <select
              className="input !h-9 !text-sm min-w-[260px]"
              value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.client ? ` · ${p.client}` : ""}
                </option>
              ))}
            </select>
          </div>
        }
      />

      {convs === null ? (
        <PageLoader />
      ) : templated.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon={<Sparkles className="h-5 w-5" />}
              title="No conversions to recommend on"
              description={selectedProject
                ? `${selectedProject.name} has no conversions with a target FBDI template yet. Bind a template, then return here.`
                : "Pick an engagement above to surface AI suggestions for its conversions."}
            />
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-4">
          {templated.map((c) => (
            <ConversionRecCard
              key={c.id}
              conversion={c}
              applied={applied}
              learned={learned}
              onApply={handleApply}
              onAuthor={setAuthoring}
              onDismiss={(id) => setApplied((prev) => new Set(prev).add(id))}
            />
          ))}
        </div>
      )}

      {authoring && (
        <RuleAuthorModal
          open
          onClose={() => setAuthoring(null)}
          conversionId={authoring.project.id}
          fields={authoring.fields}
          sourceColumns={authoring.dataset.columns}
          onSaved={() => { setAuthoring(null); flash("Rule saved & added to library"); }}
        />
      )}

      {toast && (
        <div className="pointer-events-none fixed bottom-6 right-6 rounded-md bg-ink px-4 py-2 text-xs text-white shadow-soft">
          {toast}
        </div>
      )}
    </>
  );
};

/**
 * One conversion's recommendation card. Loads its own fields + source columns
 * (dataset profile OR live EBS) and builds recommendations independently, so a
 * slow live-EBS fetch on one conversion never blocks the rest of the page.
 */
const ConversionRecCard: React.FC<{
  conversion: Conversion;
  applied: Set<string>;
  learned: Set<string>;
  onApply: (entry: ProjectRecs, rec: Recommendation, learn: boolean) => void;
  onAuthor: (entry: ProjectRecs) => void;
  onDismiss: (recId: string) => void;
}> = ({ conversion, applied, learned, onApply, onAuthor, onDismiss }) => {
  const [entry, setEntry] = useState<ProjectRecs | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const fields = await FbdiApi.fields(conversion.template_id as any);
        let dsLike: DatasetDetail;
        if (conversion.dataset_id) {
          dsLike = await DatasetsApi.get(conversion.dataset_id);
        } else {
          // EBS mode — columns stream from the live source-columns endpoint
          const sc = await ConversionsApi.sourceColumns(conversion.id);
          dsLike = { columns: sc.columns } as DatasetDetail;
        }
        if (!alive) return;
        setEntry({
          project: conversion,
          dataset: dsLike,
          fields,
          recs: buildRecommendations({ dataset: dsLike, targetFields: fields }),
        });
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => { alive = false; };
  }, [conversion.id]);

  if (failed) return null;

  if (!entry) {
    return (
      <Card>
        <CardBody>
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <Spinner /> Loading recommendations for {conversion.name}…
          </div>
        </CardBody>
      </Card>
    );
  }

  const { recs } = entry;
  return (
    <Card>
      <CardHeader
        title={
          <Link to={`/mappings?conversion=${conversion.id}`} className="hover:text-brand-dark">
            {conversion.name}
          </Link>
        }
        subtitle={recs.length ? `${recs.length} recommendation(s)` : "No recommendations — source looks clean"}
        actions={
          <div className="flex items-center gap-2">
            {conversion.template_name && <Pill tone="brand">{conversion.template_name}</Pill>}
            <button
              onClick={() => onAuthor(entry)}
              className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium text-brand-dark hover:bg-brand-subtle"
            >
              <Wand2 className="h-3 w-3" /> Custom rule
            </button>
          </div>
        }
      />
      {recs.length > 0 && (
        <CardBody>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {recs.slice(0, 6).map((r) => (
              <RecommendationCard
                key={r.id}
                rec={r}
                applied={applied.has(r.id)}
                learned={learned.has(r.id)}
                onApply={(rec, learn) => onApply(entry, rec, learn)}
                onDismiss={() => onDismiss(r.id)}
              />
            ))}
          </div>
          {recs.length > 6 && (
            <div className="mt-3 text-center">
              <Link
                to={conversion.dataset_id
                  ? `/datasets/${conversion.dataset_id}/prepare`
                  : `/mappings?conversion=${conversion.id}`}
                className="text-xs font-medium text-brand-dark hover:underline"
              >
                View all {recs.length} recommendations →
              </Link>
            </div>
          )}
        </CardBody>
      )}
    </Card>
  );
};
