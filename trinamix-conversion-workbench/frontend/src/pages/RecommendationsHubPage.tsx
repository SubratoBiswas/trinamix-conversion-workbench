import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles, Wand2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi } from "@/api";
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
  const [items, setItems] = useState<ProjectRecs[] | null>(null);
  const [authoring, setAuthoring] = useState<ProjectRecs | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const projects = await ConversionsApi.list();
      const out: ProjectRecs[] = [];
      for (const p of projects) {
        if (!p.dataset_id || !p.template_id) continue;  // planning-only — skip
        try {
          const [ds, fields] = await Promise.all([
            DatasetsApi.get(p.dataset_id),
            FbdiApi.fields(p.template_id),
          ]);
          out.push({
            project: p,
            dataset: ds,
            fields,
            recs: buildRecommendations({ dataset: ds, targetFields: fields }),
          });
        } catch { /* skip */ }
      }
      setItems(out);
    })();
  }, []);

  if (items === null) return <PageLoader />;

  const totalRecs = items.reduce((s, p) => s + p.recs.length, 0);

  return (
    <>
      <PageTitle
        title="Recommendations"
        subtitle="Cross-project AI suggestions tied to source data + FBDI target metadata"
      />

      {totalRecs === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon={<Sparkles className="h-5 w-5" />}
              title="No recommendations to action"
              description="Profile a dataset and bind it to an FBDI template — the AI engine will surface suggestions here."
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
                      <RecommendationCard key={r.id} rec={r} />
                    ))}
                  </div>
                  {recs.length > 6 && (
                    <div className="mt-3 text-center">
                      <Link
                        to={`/datasets/${project.dataset_id}/prepare`}
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
