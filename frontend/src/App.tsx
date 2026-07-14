import React, { useEffect } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppLayout } from "@/components/layout/AppLayout";
import { GlobalActivityBar } from "@/components/GlobalActivityBar";
import { useAuth } from "@/store/authStore";

import { LoginPage } from "@/pages/LoginPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { ConvertFilePage } from "@/pages/ConvertFilePage";
import { DatasetDetailPage } from "@/pages/DatasetDetailPage";
import { DatasetPreparationPage } from "@/pages/DatasetPreparationPage";
import { FbdiTemplatesPage, FbdiTemplateDetailPage } from "@/pages/FbdiTemplatesPage";
import GoldStandardsPage from "@/pages/GoldStandardsPage";

// Engagement-level pages
import { ProjectsPage } from "@/pages/ProjectsPage";
import { SetupWizard } from "@/components/setup/SetupWizard";
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
    <>
    <GlobalActivityBar />
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        {/* Overview */}
        <Route index element={<DashboardPage />} />

        {/* Data */}
        <Route path="convert"                element={<ConvertFilePage />} />
        <Route path="datasets"               element={<DatasetsPage />} />
        <Route path="datasets/:id"           element={<DatasetDetailPage />} />
        <Route path="datasets/:id/prepare"   element={<DatasetPreparationPage />} />
        <Route path="fbdi"                   element={<FbdiTemplatesPage />} />
        <Route path="fbdi/:id"               element={<FbdiTemplateDetailPage />} />
        <Route path="gold"                   element={<GoldStandardsPage />} />

        {/* Engagements */}
        <Route path="projects"               element={<ProjectsPage />} />
        <Route path="projects/new"           element={<SetupWizard />} />
        <Route path="projects/:id"           element={<ProjectOverviewPage />} />
        <Route path="projects/:id/cutover"   element={<MigrationMonitorPage />} />

        {/* Cutover landing */}
        <Route path="cutover"                element={<CutoverLanding />} />

        {/* Conversion objects */}
        <Route path="conversions"            element={<ConversionsPage />} />
        <Route path="conversions/:id"        element={<ConversionDetailPage />} />
        <Route path="conversions/:id/output" element={<OutputPreviewPage />} />

        {/* Conversion workspaces */}
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
    </>
  );
};

// ─── Output Preview landing — project-scoped conversion picker ────────────────
const OutputPreviewLanding: React.FC = () => {
  const [projects, setProjects] = React.useState<any[]>([]);
  const [conversions, setConversions] = React.useState<any[]>([]);
  const [projectId, setProjectId] = React.useState<string | null>(null);  // string ID (MongoDB)
  const [loading, setLoading] = React.useState(false);
  const loc = useLocation();
  const nav = useNavigate();

  React.useEffect(() => {
    import("@/api").then(({ ProjectsApi }) => ProjectsApi.list().then((rows: any[]) => {
      setProjects(rows);
      const qsId = new URLSearchParams(loc.search).get("project");
      const fallback = rows[0]?.id ?? null;
      // IDs are MongoDB strings — never coerce to number
      setProjectId(qsId || (fallback != null ? String(fallback) : null));
    }));
  }, []);

  React.useEffect(() => {
    if (!projectId) { setConversions([]); return; }
    setLoading(true);
    import("@/api").then(({ ProjectsApi }) =>
      ProjectsApi.conversions(String(projectId)).then((rows: any[]) => {
        setConversions(rows);
        setLoading(false);
      }).catch(() => setLoading(false))
    );
  }, [projectId]);

  const onChangeProject = (id: string) => {
    setProjectId(id);
    nav(`/output?project=${id}`, { replace: true });
  };

  const project = projects.find((p) => String(p.id) === String(projectId));
  // A conversion is previewable once it has a target FBDI template. With an
  // uploaded dataset it converts the file; without one (dataset_id null) it
  // streams live from Oracle EBS. (The conversions list response doesn't always
  // include source_type/ebs_table_hint, so don't gate on those.)
  const ready = conversions.filter((c) => c.template_id);

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
            onChange={(e) => onChangeProject(e.target.value)}  // no Number() — IDs are strings
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
            // React Router Link — soft navigation, no full-page reload
            <Link key={c.id} to={`/conversions/${c.id}/output`} className="card flex flex-col gap-2 p-4 hover:border-brand">
              <div className="text-sm font-semibold text-ink">{c.name}</div>
              <div className="text-xs text-ink-muted">
                {c.dataset_name || (c.ebs_table_hint ? `EBS · ${c.ebs_table_hint}` : "Oracle EBS")} → {c.template_name}
              </div>
              <div className="text-[11px] text-ink-muted">{c.status}</div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
};

// ─── Cutover landing — explicit project picker ────────────────────────────────
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
          Pick an engagement to open its cutover board.
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
