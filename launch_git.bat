@echo off
REM ---------------------------------------------------------------------------
REM The commit message lives in COMMIT_MSG.txt and is passed with -F, NOT -m.
REM cmd.exe caps a command line at 8191 characters. The message had grown to
REM 39,147, so `git commit -m "..."` never launched -- "The system cannot
REM execute the specified program" -- and the script went straight on to push,
REM which reported "Everything up-to-date" because nothing had been committed.
REM A run that commits nothing and still prints DONE is the worst possible
REM shape, so the commit is now verified before pushing. Never put the message
REM back on the command line.
REM ---------------------------------------------------------------------------
cd /d "C:\Users\SubratoBiswas\trinamix-conversion-workbench"
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__ 1>nul 2>nul
echo Removing scratch files (QA renders, Excel lock files)...
rmdir /s /q "_qa" 2>nul
del /f /q "~$*.xlsx" "~$*.pptx" 2>nul
git rm -r --cached --ignore-unmatch _qa 1>nul 2>nul
echo Staging...
git add -A
echo Committing (no-op if nothing changed)...
if not exist "COMMIT_MSG.txt" (
  echo.
  echo ****** STOPPED: COMMIT_MSG.txt is missing. Nothing was committed. ******
  pause
  exit /b 1
)
git commit -F "COMMIT_MSG.txt"
git diff --cached --quiet
if errorlevel 1 (
  echo.
  echo ****** STOPPED: changes are STILL STAGED, so the commit did not happen.
  echo ****** Nothing has been pushed. Read the git error above.
  pause
  exit /b 1
)
echo Pushing...
git push origin main
if errorlevel 1 (
  echo.
  echo ****** PUSH FAILED - the deploy did NOT happen. ******
  pause
  exit /b 1
)
echo.
echo ====== DONE - committed and pushed. Press any key to close ======
pause
