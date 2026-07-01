@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
git add frontend/src/pages/ProjectOverviewPage.tsx
git commit -m "fix: ProjectOverviewPage — add LifecycleTracker, ExecSummaryCard, CutoverPanel, DiscoveryPanel"
git push origin main
pause
