@echo off
cd /d "C:\Users\SubratoBiswas\trinamix-conversion-workbench"
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
echo Removing stray files from the previous multi-line-commit misparse...
del /f /q "Payee" 2>nul
del /f /q "Corporation)'" 2>nul
git rm --cached --ignore-unmatch "Payee" "Corporation)'" 2>nul
echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__
echo Staging...
git add -A
echo Committing (no-op if clean)...
git commit -m "Clean up stray files from earlier multi-line commit misparse and record the mapping-intelligence + document-review work. Single-line message on purpose: Windows batch treats newlines and the arrow character as commands and redirects, which had created the empty files Payee and Corporation. Full change notes live in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
