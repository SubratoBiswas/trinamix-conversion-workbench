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

// Tiny landing page for /output that nudges the user to pick a conversion.
const OutputPreviewLanding: React.FC = () => {
  const [items, setItems] = React.useState<any[]>([]);
  React.useEffect(() => {
    import("@/api").then(({ ConversionsApi }) => ConversionsApi.list().then(setItems));
  }, []);
  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-ink">Output Preview</h1>
        <p className="mt-1 text-sm text-ink-muted">Select a conversion to preview its converted FBDI output.</p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {items.filter(c => c.dataset_id && c.template_id).map((c) => (
          <a key={c.id} href={`/conversions/${c.id}/output`} className="card flex flex-col gap-2 p-4 hover:border-brand">
            <div className="text-sm font-semibold">{c.name}</div>
            <div className="text-xs text-ink-muted">{c.dataset_name} → {c.template_name}</div>
            <div className="text-[11px] text-ink-muted">{c.status}</div>
          </a>
        ))}
      </div>
    </>
  );
};

// Cutover landing — auto-redirects to the latest active engagement's monitor.
const CutoverLanding: React.FC = () => {
  const nav = useNavigate();
  React.useEffect(() => {
    import("@/api").then(({ ProjectsApi }) =>
      ProjectsApi.list().then((ps: any[]) => {
        const active = ps.find((p) => p.status === "in_progress") || ps[0];
        if (active) nav(`/projects/${active.id}/cutover`, { replace: true });
      })
    );
  }, [nav]);
  return (
    <div className="flex h-64 items-center justify-center text-sm text-ink-muted">
      Loading migration monitor…
    </div>
  );
};

export default App;
