@echo off
cd C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo Killing all git and cmd processes...
taskkill /F /IM git.exe 2>nul
taskkill /F /IM git-remote-https.exe 2>nul
timeout /t 2 /nobreak >nul
del /f .git\index.lock 2>nul
del /f .git\HEAD.lock 2>nul
echo.
echo Staging files...
git add backend/app/routers/copilot.py
git add frontend/src/api/index.ts
git add "frontend/src/components/recommendations/RecommendationCard.tsx"
echo.
echo Committing...
git commit -m "add AI suggest-default and inline default value input on fill_missing recommendations"
echo.
echo Pushing...
git push origin main
echo.
echo DONE
pause
