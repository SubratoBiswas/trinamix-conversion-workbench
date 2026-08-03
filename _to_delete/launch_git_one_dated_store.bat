@echo off
setlocal
REM ---------------------------------------------------------------------------
REM One-click deploy for the ONE DATED STORE change.
REM
REM Does NOT replace launch_git.bat and does NOT touch COMMIT_MSG.txt. It uses
REM its own message file so your running commit log stays as it is.
REM
REM Order matters and each step stops on failure, because the failure mode this
REM repo keeps hitting is a script that did nothing and still printed DONE:
REM   1. apply the patch (skipped if it is already applied)
REM   2. run the backend tests
REM   3. commit with -F, then VERIFY the commit actually happened
REM   4. push
REM
REM The message is passed with -F, never -m. cmd.exe caps a command line at
REM 8,191 characters; when the message grew past that, git commit failed to
REM launch, the script went straight on to push, and reported
REM "Everything up-to-date" -- a silent no-commit that still printed DONE.
REM
REM This script lives IN the repo, so it works out its own location rather than
REM hard-coding one. Clone the repo anywhere, or move the folder, and it still
REM runs -- an absolute path baked into a tracked file is a trap for whoever
REM checks it out on a different machine.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

REM Confirm we are actually at the top of the repo before touching anything.
if not exist ".git" (
  echo ****** STOPPED: no .git here. Put this script in the repo root. ******
  echo        Looked in: %CD%
  pause
  exit /b 1
)

set "PATCH=one-dated-store.patch"
set "MSG=COMMIT_MSG_ONE_DATED_STORE.txt"

echo.
echo ================= ONE DATED STORE - deploy =================
echo.

if not exist "%PATCH%" (
  echo ****** STOPPED: %PATCH% is not in the repo root. Nothing done. ******
  pause
  exit /b 1
)
if not exist "%MSG%" (
  echo ****** STOPPED: %MSG% is missing. Nothing done. ******
  pause
  exit /b 1
)

REM A stale index.lock blocks every git command with "Unable to create
REM '.git/index.lock': File exists" and reads like the repo is broken. Usually
REM it is just a crashed or interrupted git. Clearing it here is safe as long as
REM no other git process is running.
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul

REM ---- 1. Apply the patch -----------------------------------------------------
REM --check first so a re-run of this script is harmless. If the patch is already
REM in the tree, applying it again would fail and look like a real error.
echo.
echo [1/4] Applying %PATCH% ...
git apply --check "%PATCH%" 1>nul 2>nul
if errorlevel 1 (
  git apply --reverse --check "%PATCH%" 1>nul 2>nul
  if errorlevel 1 (
    echo.
    echo ****** STOPPED: the patch will not apply cleanly and is not already
    echo ****** applied. Your working tree has diverged from the baseline it
    echo ****** was cut against. Nothing has been changed, committed or pushed.
    echo.
    echo Show me the output of:  git apply --3way %PATCH%
    pause
    exit /b 1
  )
  echo       already applied - skipping.
) else (
  git apply "%PATCH%"
  if errorlevel 1 (
    echo ****** STOPPED: git apply failed. Nothing committed. ******
    pause
    exit /b 1
  )
  echo       applied.
)

REM ---- 2. Tests ---------------------------------------------------------------
REM 843 should pass. A push that skips this is a push that ships the two-stores
REM bug back in under a new name.
echo.
echo [2/4] Running the backend tests (about two minutes)...
set "PY="
if exist "backend\venv\Scripts\python.exe" set "PY=backend\venv\Scripts\python.exe"
if not defined PY (
  where python 1>nul 2>nul && set "PY=python"
)
if not defined PY (
  echo       No Python found - SKIPPING tests.
  echo       Run them yourself before trusting this deploy:
  echo         cd backend ^&^& python -m pytest tests -q
  choice /c YN /m "Continue and push WITHOUT running tests"
  if errorlevel 2 (
    echo Stopped at your request. The patch is applied but nothing is committed.
    pause
    exit /b 1
  )
) else (
  pushd backend
  "..\%PY%" -m pytest tests -q -p no:cacheprovider
  if errorlevel 1 (
    popd
    echo.
    echo ****** STOPPED: tests failed. Nothing committed, nothing pushed. ******
    echo ****** The patch IS applied in your working tree - fix or revert with:
    echo ******   git apply --reverse %PATCH%
    pause
    exit /b 1
  )
  popd
  echo       tests passed.
)

REM ---- 3. Commit --------------------------------------------------------------
echo.
echo [3/4] Staging and committing...
REM This script, its message file and the patch are staged too, so the repo
REM carries the thing that deployed it.
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__ 1>nul 2>nul
git add -A

echo.
echo ---- Staged for this commit ----
git diff --cached --name-only
echo --------------------------------

git commit -F "%MSG%"
REM VERIFY. If anything is still staged, the commit did not happen and pushing
REM now would report "Everything up-to-date" and look like success.
git diff --cached --quiet
if errorlevel 1 (
  echo.
  echo ****** STOPPED: changes are STILL STAGED, so the commit did not happen.
  echo ****** Nothing has been pushed. Read the git error above.
  pause
  exit /b 1
)
echo       committed.

REM ---- 4. Push ----------------------------------------------------------------
echo.
echo [4/4] Pushing to origin/main...
git push origin main
if errorlevel 1 (
  echo.
  echo ****** PUSH FAILED - the deploy did NOT happen. ******
  pause
  exit /b 1
)

for /f %%i in ('git rev-parse --short HEAD') do set SHA=%%i
echo.
echo ====== DONE - committed and pushed ======
echo   Pushed commit : %SHA%
echo   Now check     : https://trinamix-conversion-backend.onrender.com/api/health
echo   Deployed when the "commit" field there starts with %SHA%
echo   (Render takes a few minutes to build. The free tier cold-starts, so the
echo    first request after idle takes about 45 seconds - that is not your bug.)
echo.
echo   After it is live, run the backfill once:
echo     POST /api/learned-mappings/backfill-dated-store
echo   It also runs on every boot, so this is only to see the counts.
echo.
pause
endlocal
