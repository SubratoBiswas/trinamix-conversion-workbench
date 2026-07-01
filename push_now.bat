@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
del /f .git\index.lock 2>nul
git add frontend/src/pages/ProjectOverviewPage.tsx
git commit -m "fix: ProjectOverviewPage full rich UI — LifecycleTracker + ExecSummary + CutoverPanel + Discovery"
git push origin main
echo DONE - press any key
pause
