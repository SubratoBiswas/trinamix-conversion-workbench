@echo off
cd C:\Users\SubratoBiswas\trinamix-conversion-workbench
taskkill /F /IM git.exe 2>nul
del /f .git\index.lock 2>nul
del /f .git\HEAD.lock 2>nul
git add backend/app/routers/copilot.py
git add frontend/src/api/index.ts
git add frontend/src/components/recommendations/RecommendationCard.tsx
git commit -m "add AI suggest-default and inline default value input on fill_missing recommendations"
git push origin main
pause
