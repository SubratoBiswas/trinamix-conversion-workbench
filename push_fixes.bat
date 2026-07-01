@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
git add frontend/src/pages/ProjectOverviewPage.tsx
git add frontend/src/pages/ApprovalsPage.tsx
git add frontend/src/pages/RecommendationsHubPage.tsx
git add frontend/src/pages/LoadDashboardPage.tsx
git add frontend/src/pages/MappingReviewPage.tsx
git add frontend/src/components/cutover/ExecSummaryCard.tsx
git add frontend/src/components/cutover/CutoverPanel.tsx
git add frontend/src/components/discovery/DiscoveryPanel.tsx
git add frontend/src/components/source/SourceConnectionCard.tsx
git commit -m "fix: ProjectOverview full UI (lifecycle + exec summary + cutover panel + discovery)"
git push origin main
pause
