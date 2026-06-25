import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles, Wand2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, MappingApi, LearningApi, ProjectsApi } from "@/api";
import {
  Card, CardBody, CardHeader, EmptyState, PageLoader, PageTitle, Pill,
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
 * Cross-project recommendations hub. Walks each project, runs the
 * frontend recommendation engine against its dataset + target FBDI metadata,
 * and shows the consolidated feed.
 */
export const RecommendationsHubPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [items, setItems] = useState<ProjectRecs[] | null>(null);
  const [authoring, setAuthoring] = useState<ProjectRecs | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const [learned, setLearned] = useState<Set<string>>(new Set());

  const handleApply = async (entry: ProjectRecs, rec: import("@/lib/recommendations").Recommendation, learn: boolean) => {
    try {
      await MappingApi.addRule(entry.project.id, {
        source_column: rec.column,
        rule_type: rec.ruleType || rec.kind,
        config: rec.config || {},
        description: rec.title,
      });
      setApplied(prev => new Set(prev).add(rec.id));
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
          });
          setLearned(prev => new Set(prev).add(rec.id));
        } catch { /* learn failure is non-fatal */ }
      }
      setToast(learn ? "Rule applied & learned" : "Rule applied");
      setTimeout(() => setToast(null), 2400);
    } catch {
      setToast("Failed to apply rule");
      setTimeout(() => setToast(null), 2400);
    }
  };

  // Load the engagement list once and pick an initial project (URL ?project= wins).
  useEffect(() => {
    ProjectsApi.list().then((ps) => {
      setProjects(ps);
      const qs = new URLSearchParams(window.location.search).get("project");
      setProjectId(qs || (ps[0] ? String(ps[0].id) : null));
    }).catch(() => setProjects([]));
  }, []);

  // Build recommendations for every conversion in the SELECTED project. Works
  // for dataset-backed conversions and EBS conversions (columns come from the
  // live source-columns endpoint rather than an uploaded file).
  useEffect(() => {
    if (!projectId) { setItems([]); return; }
    setItems(null);
    (async () => {
      try {
        const convs = await ProjectsApi.conversions(String(projectId));
        const out: ProjectRecs[] = [];
        for (const c of convs) {
          if (!c.template_id) continue;  // no target template — nothing to suggest against
          try {
            const fields = await FbdiApi.fields(c.template_id);
            let dsLike: DatasetDetail;
            if (c.dataset_id) {
              dsLike = await DatasetsApi.get(c.dataset_id);
            } else {
              // EBS mode — synthesize a dataset-shaped object from live columns
              const sc = await ConversionsApi.sourceColumns(c.id);
              dsLike = { columns: sc.columns } as DatasetDetail;
            }
            out.push({
              project: c,
              dataset: dsLike,
              fields,
              recs: buildRecommendations({ dataset: dsLike, targetFields: fields }),
            });
          } catch { /* skip individual conversion errors */ }
        }
        setItems(out);
      } catch {
        setItems([]);
      }
    })();
  }, [projectId]);

  if (items === null) return <PageLoader />;

  const selectedProject = projects.find((p) => String(p.id) === String(projectId));

  const totalRecs = items.reduce((s, p) => s + p.recs.length, 0);

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

      {totalRecs === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon={<Sparkles className="h-5 w-5" />}
              title="No recommendations for this engagement"
              description={selectedProject
                ? `No suggestions for ${selectedProject.name} right now — pick another engagement above, or its conversions may already be clean and bound.`
                : "Pick an engagement above to surface AI suggestions for its conversions."}
            />
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-4">
          {items.filter((g) => g.recs.length > 0).map((entry) => {
            const { project, recs } = entry;
            return (
              <Card key={project.id}>
                <CardHeader
                  title={
                    <Link to={`/mappings?project=${project.id}`} className="hover:text-brand-dark">
                      {project.name}
                    </Link>
                  }
                  subtitle={`${recs.length} recommendation(s)`}
                  actions={
                    <div className="flex items-center gap-2">
                      <Pill tone="brand">{project.template_name}</Pill>
                      <button
                        onClick={() => setAuthoring(entry)}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-white px-2 py-1 text-[11px] font-medium text-brand-dark hover:bg-brand-subtle"
                      >
                        <Wand2 className="h-3 w-3" /> Custom rule
                      </button>
                    </div>
                  }
                />
                <CardBody>
                  <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                    {recs.slice(0, 6).map((r) => (
                      <RecommendationCard
                        key={r.id}
                        rec={r}
                        applied={applied.has(r.id)}
                        learned={learned.has(r.id)}
                        onApply={(rec, learn) => handleApply(entry, rec, learn)}
                        onDismiss={() => setApplied(prev => new Set(prev).add(r.id))}
                      />
                    ))}
                  </div>
                  {recs.length > 6 && (
                    <div className="mt-3 text-center">
                      <Link
                        to={project.dataset_id
                          ? `/datasets/${project.dataset_id}/prepare`
                          : `/mappings?conversion=${project.id}`}
                        className="text-xs font-medium text-brand-dark hover:underline"
                      >
                        View all {recs.length} recommendations →
                      </Link>
                    </div>
                  )}
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}

      {authoring && (
        <RuleAuthorModal
          open
          onClose={() => setAuthoring(null)}
          conversionId={authoring.project.id}
          fields={authoring.fields}
          sourceColumns={authoring.dataset.columns}
          onSaved={() => {
            setAuthoring(null);
            setToast("Rule saved & added to library");
            setTimeout(() => setToast(null), 2400);
          }}
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
