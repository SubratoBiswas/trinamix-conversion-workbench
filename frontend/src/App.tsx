import React, { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppLayout } from "@/components/layout/AppLayout";
import { useAuth } from "@/store/authStore";

import { LoginPage } from "@/pages/LoginPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { DatasetDetailPage } from "@/pages/DatasetDetailPage";
import { DatasetPreparationPage } from "@/pages/DatasetPreparationPage";
import { FbdiTemplatesPage, FbdiTemplateDetailPage } from "@/pages/FbdiTemplatesPage";

// Engagement-level pages
import { ProjectsPage, NewProjectPage } from "@/pages/ProjectsPage";
import { ProjectOverviewPage } from "@/pages/ProjectOverviewPage";

// Conversion-level pages
import { ConversionsPage } from "@/pages/ConversionsPage";
import { ConversionDetailPage } from "@/pages/ConversionDetailPage";
import { MigrationMonitorPage } from "@/pages/MigrationMonitorPage";

import { MappingReviewPage } from "@/pages/MappingReviewPage";
import { TransformationStudioPage } from "@/pages/TransformationStudioPage";
import { CleansingPage, ValidationPage } from "@/pages/QualityPages";
import { OutputPreviewPage } from "@/pages/OutputPreviewPage";
import { LoadDashboardPage } from "@/pages/LoadDashboardPage";
import { DependencyGraphPage } from "@/pages/DependencyGraphPage";
import { ErrorTracebackPage } from "@/pages/ErrorTracebackPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";
import { WorkflowBuilderPage } from "@/pages/WorkflowBuilderPage";
import { AuditPage } from "@/pages/AuditPage";
import { LearningCenterPage } from "@/pages/LearningCenterPage";
import { RuleLibraryPage } from "@/pages/RuleLibraryPage";
import { CrosswalkLibraryPage } from "@/pages/CrosswalkLibraryPage";
import { RecommendationsHubPage } from "@/pages/RecommendationsHubPage";
import { ApprovalsPage } from "@/pages/ApprovalsPage";

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const token = useAuth((s) => s.token);
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: location }} />;
  return <>{children}</>;
};

const App: React.FC = () => {
  const hydrate = useAuth((s) => s.hydrate);
  useEffect(() => { hydrate(); }, [hydrate]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        {/* Overview */}
        <Route index element={<DashboardPage />} />

        {/* Data */}
        <Route path="datasets"               element={<DatasetsPage />} />
        <Route path="datasets/:id"           element={<DatasetDetailPage />} />
        <Route path="datasets/:id/prepare"   element={<DatasetPreparationPage />} />
        <Route path="fbdi"                   element={<FbdiTemplatesPage />} />
        <Route path="fbdi/:id"               element={<FbdiTemplateDetailPage />} />

        {/* Engagements */}
        <Route path="projects"               element={<ProjectsPage />} />
        <Route path="projects/new"           element={<NewProjectPage />} />
        <Route path="projects/:id"           element={<ProjectOverviewPage />} />
        <Route path="projects/:id/cutover"   element={<MigrationMonitorPage />} />

        {/* Cutover landing — picks the first active engagement */}
        <Route path="cutover"                element={<CutoverLanding />} />

        {/* Conversion objects */}
        <Route path="conversions"            element={<ConversionsPage />} />
        <Route path="conversions/:id"        element={<ConversionDetailPage />} />
        <Route path="conversions/:id/output" element={<OutputPreviewPage />} />

        {/* Conversion workspaces (operate on a conversion via ?conversion= query param) */}
        <Route path="mappings"               element={<MappingReviewPage />} />
        <Route path="transformations"        element={<TransformationStudioPage />} />
        <Route path="recommendations"        element={<RecommendationsHubPage />} />
        <Route path="output"                 element={<OutputPreviewLanding />} />

        {/* Quality */}
        <Route path="cleansing"              element={<CleansingPage />} />
        <Route path="validation"             element={<ValidationPage />} />

        {/* Load Management */}
        <Route path="load"                   element={<LoadDashboardPage />} />
        <Route path="load/errors"            element={<ErrorTracebackPage />} />
        <Route path="dependencies"           element={<DependencyGraphPage />} />

        {/* Workflows */}
        <Route path="workflows"              element={<WorkflowsPage />} />
        <Route path="workflows/:id"          element={<WorkflowBuilderPage />} />

        {/* AI Engine */}
        <Route path="learning"               element={<LearningCenterPage />} />
        <Route path="rules"                  element={<RuleLibraryPage />} />
        <Route path="crosswalks"             element={<CrosswalkLibraryPage />} />

        {/* Compliance */}
        <Route path="audit"                  element={<AuditPage />} />
        <Route path="approvals"              element={<ApprovalsPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

// Output Preview landing — project-scoped picker. Two engagements
// implementing SCM can each have an "Item Master" conversion; without
// the project layer the user sees both and can't tell which is which.
// The URL carries ?project=N so deep-links and back-button work.
const OutputPreviewLanding: React.FC = () => {
  const [projects, setProjects] = React.useState<any[]>([]);
  const [conversions, setConversions] = React.useState<any[]>([]);
  const [projectId, setProjectId] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(false);
  const loc = useLocation();
  const nav = useNavigate();

  React.useEffect(() => {
    import("@/api").then(({ ProjectsApi }) => ProjectsApi.list().then((rows: any[]) => {
      setProjects(rows);
      const qsId = new URLSearchParams(loc.search).get("project");
      const fallback = rows[0]?.id ?? null;
      setProjectId(qsId ? Number(qsId) : fallback);
    }));
  }, []);

  React.useEffect(() => {
    if (!projectId) { setConversions([]); return; }
    setLoading(true);
    import("@/api").then(({ ProjectsApi }) =>
      ProjectsApi.conversions(projectId).then((rows: any[]) => {
        setConversions(rows);
        setLoading(false);
      }).catch(() => setLoading(false))
    );
  }, [projectId]);

  const onChangeProject = (id: number) => {
    setProjectId(id);
    nav(`/output?project=${id}`, { replace: true });
  };

  const project = projects.find((p) => p.id === projectId);
  const ready = conversions.filter((c) => c.dataset_id && c.template_id);

  return (
    <>
      <div className="mb-5 flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Output Preview</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Pick an engagement, then a conversion, to preview its converted FBDI output.
          </p>
        </div>
        <div>
          <label className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
            Engagement
          </label>
          <select
            className="input !h-9 !text-sm min-w-[260px]"
            value={projectId ?? ""}
            onChange={(e) => onChangeProject(Number(e.target.value))}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.client ? ` · ${p.client}` : ""}
              </option>
            ))}
          </select>
        </div>
      </div>

      {project && (
        <div className="mb-4 rounded-md border border-line bg-canvas px-3 py-2 text-[12px] text-ink-muted">
          Source: <span className="font-mono text-ink">{project.source_system || "—"}</span>
          {Array.isArray(project.selected_modules) && project.selected_modules.length > 0 && (
            <>
              {" · "}Scope: <span className="text-ink">{project.selected_modules.join(", ")}</span>
            </>
          )}
          {" · "}Conversions: <span className="font-mono text-ink">{conversions.length}</span>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-ink-muted">Loading conversions…</div>
      ) : ready.length === 0 ? (
        <div className="rounded-md border border-line bg-white px-4 py-6 text-center text-sm text-ink-muted">
          No conversions ready to preview yet. Bind a dataset + FBDI template to a conversion on the engagement first.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {ready.map((c) => (
            <a key={c.id} href={`/conversions/${c.id}/output`} className="card flex flex-col gap-2 p-4 hover:border-brand">
              <div className="text-sm font-semibold text-ink">{c.name}</div>
              <div className="text-xs text-ink-muted">{c.dataset_name} → {c.template_name}</div>
              <div className="text-[11px] text-ink-muted">{c.status}</div>
            </a>
          ))}
        </div>
      )}
    </>
  );
};

// Cutover landing — explicit project picker. Auto-redirecting to the
// "first active engagement" was misleading because the user couldn't
// tell the page had silently chosen one project out of many.
const CutoverLanding: React.FC = () => {
  const [projects, setProjects] = React.useState<any[]>([]);
  const nav = useNavigate();
  React.useEffect(() => {
    import("@/api").then(({ ProjectsApi }) =>
      ProjectsApi.list().then((rows: any[]) => setProjects(rows))
    );
  }, []);
  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-ink">Migration Monitor</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Pick an engagement to open its cutover board — environments × entities
          × processes × integrations, with safeguards and recon side-by-side.
        </p>
      </div>
      {projects.length === 0 ? (
        <div className="rounded-md border border-line bg-canvas px-4 py-6 text-center text-sm text-ink-muted">
          No engagements yet. Create one from <span className="font-medium">Projects</span>.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => nav(`/projects/${p.id}/cutover`)}
              className="card flex flex-col items-start gap-1.5 p-4 text-left hover:border-brand"
            >
              <div className="text-sm font-semibold text-ink">{p.name}</div>
              <div className="text-xs text-ink-muted">{p.client || "—"}</div>
              <div className="mt-1 text-[11px] text-ink-muted">
                {p.source_system || "—"} ·{" "}
                {Array.isArray(p.selected_modules) && p.selected_modules.length
                  ? p.selected_modules.join(", ")
                  : "no scope set"}
              </div>
            </button>
          ))}
        </div>
      )}
    </>
  );
};

export default App;
