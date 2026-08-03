@echo off
setlocal enabledelayedexpansion
REM ===========================================================================
REM THE deploy script. One file, clicked, does the whole thing.
REM
REM   0. work out where the repo is (this script lives in it)
REM   1. apply any pending .patch sitting in the root
REM   2. stage, commit with -F, and VERIFY the commit actually happened
REM   3. push
REM
REM DEPLOY ONLY -- it does not run the test suite.
REM
REM That is a deliberate choice, not an oversight, and it is only safe because
REM of where the testing moved to: every patch dropped in this repo has already
REM had the full suite run against it before it was cut. This machine has no
REM Windows venv and no backend dependencies installed, so the step could never
REM do more here than print "cannot run" and ask whether to continue -- a prompt
REM that adds a keypress and proves nothing. Worse, when it went wrong it
REM reported "tests failed", which said something false about the code.
REM
REM If you ever DO want them run locally:
REM   python -m pip install -r backend\requirements.txt
REM   cd backend ^&^& python -m pytest tests -q
REM
REM Every step below stops on failure and says what state it left you in,
REM because the shape this repo keeps hitting is a script that did nothing and
REM still printed DONE.
REM
REM The message is passed with -F, NEVER -m. cmd.exe caps a command line at
REM 8,191 characters. The message had grown to 39,147, so `git commit -m "..."`
REM never launched -- "The system cannot execute the specified program" -- and
REM the script went straight on to push, which reported "Everything up-to-date"
REM because nothing had been committed. Never put the message back on the
REM command line.
REM ===========================================================================

REM ---- 0. Locate the repo -----------------------------------------------------
REM Its own folder, not a hard-coded path: this file is tracked, so a path baked
REM into it is a trap for whoever clones the repo somewhere else.
cd /d "%~dp0"
if not exist ".git" (
  echo ****** STOPPED: no .git here, so this is not the repo root. ******
  echo        Looked in: %CD%
  pause
  exit /b 1
)

set "MSG=COMMIT_MSG.txt"
set "PATCHED="

REM NO PAGER. `git diff --cached --name-only` on a long list opens `less`, which
REM stops the script dead at a `:` prompt with no explanation -- it looks exactly
REM like the deploy finished, and it has not even committed yet. A batch script
REM has no business opening an interactive pager.
set "GIT_PAGER=cat"
set "PAGER=cat"

echo.
echo ===================== DEPLOY =====================
echo   Repo: %CD%
echo.

if not exist "%MSG%" (
  echo ****** STOPPED: %MSG% is missing. Nothing was committed. ******
  pause
  exit /b 1
)

REM A stale index.lock blocks every git command with "Unable to create
REM '.git/index.lock': File exists" and reads like the repo is broken. It is
REM usually just an interrupted git. Safe to clear when no other git is running.
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul

echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__ 1>nul 2>nul

echo Removing scratch files (QA renders, Excel lock files)...
rmdir /s /q "_qa" 2>nul
del /f /q "~$*.xlsx" "~$*.pptx" 2>nul
git rm -r --cached --ignore-unmatch _qa 1>nul 2>nul

REM ---- 1. Apply any pending patch ---------------------------------------------
REM --check first, so re-running this script is harmless: a patch already in the
REM tree would otherwise fail to apply and look like a real error.
echo.
echo [1/3] Pending patches...
set "FOUND="
for %%P in (*.patch) do (
  set "FOUND=1"
  git apply --check "%%P" 1>nul 2>nul
  if errorlevel 1 (
    git apply --reverse --check "%%P" 1>nul 2>nul
    if errorlevel 1 (
      echo.
      echo ****** STOPPED: %%P neither applies cleanly nor is fully applied.
      echo ****** Nothing has been changed, committed or pushed.
      echo.
      REM Say WHICH file and why. "Your tree has diverged" is true of a genuine
      REM conflict and equally true of a patch whose first half you already
      REM deployed -- and those need opposite responses. Printing the real
      REM reason is the difference between a five-second fix and an hour.
      echo ---- what git actually objected to ----
      git apply --check -v "%%P" 2>&1
      echo ---------------------------------------
      echo.
      echo If it says "already exists in working directory", this patch is
      echo PARTLY applied - you deployed an earlier one that overlaps it. Ask
      echo for a patch of only what is still missing; do not force this one.
      echo.
      echo Otherwise your tree really has moved on from the baseline it was cut
      echo against.  Try:  git apply --3way %%P
      pause
      exit /b 1
    ) else (
      echo       %%P - already applied, skipping.
    )
  ) else (
    git apply "%%P"
    if errorlevel 1 (
      echo ****** STOPPED: git apply failed on %%P. Nothing committed. ******
      pause
      exit /b 1
    )
    echo       %%P - applied.
    set "PATCHED=1"
  )
)
if not defined FOUND echo       none found - nothing to apply.

REM ---- 2. Commit --------------------------------------------------------------
echo.
echo [2/3] Staging and committing...
git add -A

echo.
echo ---- Staged for this commit ----
git --no-pager diff --cached --name-only
echo --------------------------------

git --no-pager commit -F "%MSG%"
REM VERIFY. If anything is still staged the commit did not happen, and pushing
REM now would report "Everything up-to-date" and read as success.
git --no-pager diff --cached --quiet
if errorlevel 1 (
  echo.
  echo ****** STOPPED: changes are STILL STAGED, so the commit did not happen.
  echo ****** Nothing has been pushed. Read the git error above.
  if defined PATCHED (
    echo ****** The patch IS applied in your working tree. Revert it with:
    for %%P in (*.patch) do echo ******   git apply --reverse %%P
  )
  pause
  exit /b 1
)
echo       committed.

REM ---- 3. Push ----------------------------------------------------------------
echo.
echo [3/3] Pushing to origin/main...
git push origin main
if errorlevel 1 (
  echo.
  echo ****** PUSH FAILED - the deploy did NOT happen. ******
  pause
  exit /b 1
)

REM The commit that was just pushed. Render builds this exact SHA and
REM /api/health reports the SHA it is running -- so "is my fix live yet?" is a
REM comparison, not a guess. They match => deployed. They differ => still
REM building, wait and refresh.
for /f %%i in ('git rev-parse --short HEAD') do set SHA=%%i
echo.
echo ====== DONE - committed and pushed ======
echo   Pushed commit : %SHA%
echo   Now check     : https://trinamix-conversion-backend.onrender.com/api/health
echo   Deployed when the "commit" field there starts with %SHA%
echo   (Render takes a few minutes. The free tier cold-starts, so the first
echo    request after idle takes about 45 seconds - that is not your bug.)
echo.
echo   Reminder - not fixed by any deploy, it is a Render dashboard setting:
echo     Static site -^> Redirects/Rewrites -^> source /*  destination /index.html
echo     action Rewrite.  Without it, refreshing or pasting a deep link returns
echo     404 with a blank body, which looks just like a broken page.
echo.
pause
endlocal
