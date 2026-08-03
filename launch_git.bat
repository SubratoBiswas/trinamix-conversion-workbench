@echo off
setlocal enabledelayedexpansion
REM ===========================================================================
REM THE deploy script. One file, clicked, does the whole thing.
REM
REM   0. work out where the repo is (this script lives in it)
REM   1. apply any pending .patch, if one is sitting in the root
REM   2. run the backend tests
REM   3. stage, commit with -F, and VERIFY the commit actually happened
REM   4. push
REM
REM Every step stops on failure, and each failure says what state it left you
REM in, because the shape this repo keeps hitting is a script that did nothing
REM and still printed DONE.
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

REM ---- 1. Apply a pending patch ----------------------------------------------
REM --check first, so re-running this script is harmless: a patch already in the
REM tree would otherwise fail to apply and look like a real error.
echo.
echo [1/4] Pending patches...
set "FOUND="
for %%P in (*.patch) do (
  set "FOUND=1"
  git apply --check "%%P" 1>nul 2>nul
  if errorlevel 1 (
    git apply --reverse --check "%%P" 1>nul 2>nul
    if errorlevel 1 (
      echo.
      echo ****** STOPPED: %%P will not apply cleanly and is not already applied.
      echo ****** Your working tree has diverged from the baseline it was cut
      echo ****** against. Nothing has been changed, committed or pushed.
      echo.
      echo Try:  git apply --3way %%P
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

REM ---- 2. Tests ---------------------------------------------------------------
REM A push that skips this is a push that ships a regression under a new name.
echo.
echo [2/4] Backend tests...

REM Find an interpreter. A Windows venv first because it has the pinned deps,
REM then the py launcher, then whatever python is on PATH. Note the venv checked
REM into this repo is a LINUX one (bin/, lib/) -- there is no Scripts\python.exe
REM in it, so on Windows this check simply misses and we fall through.
REM
REM The path is made ABSOLUTE here so it still resolves after the pushd into
REM backend. Prefixing "..\" to a bare `python` produced
REM "..\python is not recognized", which was then reported as "tests failed" --
REM telling you the wrong thing entirely about the state of your code.
set "PY="
if exist "%CD%\backend\venv\Scripts\python.exe" set "PY=%CD%\backend\venv\Scripts\python.exe"
if not defined PY (
  where py 1>nul 2>nul && set "PY=py"
)
if not defined PY (
  where python 1>nul 2>nul && set "PY=python"
)

set "RUNTESTS=1"
if not defined PY (
  echo       No Python found on this machine.
  set "RUNTESTS="
) else (
  echo       Using: !PY!
  "!PY!" -c "import pytest" 1>nul 2>nul
  if errorlevel 1 (
    echo       pytest is not installed for that interpreter.
    echo       Install it with:  "!PY!" -m pip install pytest
    set "RUNTESTS="
  ) else (
    REM Can the suite even import the app? Missing fastapi / beanie / pandas
    REM gives a wall of collection errors that LOOK like the change broke
    REM something. "I cannot run these" and "these fail" are different facts and
    REM must not be reported as the same one.
    pushd backend
    "!PY!" -c "import app.main" 1>nul 2>nul
    if errorlevel 1 (
      echo       The backend dependencies are not installed for that interpreter.
      echo       Install them with:
      echo         "!PY!" -m pip install -r backend\requirements.txt
      set "RUNTESTS="
    )
    popd
  )
)

if not defined RUNTESTS (
  REM NOT the same thing as a test failure, and it must not be reported as one.
  echo.
  echo       Tests were NOT run - the tools to run them are not on this machine.
  echo       That is NOT the same as a passing suite, and it is not a failure
  echo       of your code either. Nothing has been proven either way here.
  choice /c YN /m "Continue and push WITHOUT running the tests"
  if errorlevel 2 (
    echo.
    echo Stopped at your request. Nothing committed, nothing pushed.
    if defined PATCHED echo The patch IS applied in your working tree.
    pause
    exit /b 1
  )
) else (
  pushd backend
  "!PY!" -m pytest tests -q -p no:cacheprovider
  if errorlevel 1 (
    popd
    echo.
    echo ****** STOPPED: tests FAILED. Nothing committed, nothing pushed. ******
    if defined PATCHED (
      echo ****** The patch IS applied in your working tree. Revert it with:
      for %%P in (*.patch) do echo ******   git apply --reverse %%P
    )
    pause
    exit /b 1
  )
  popd
  echo       tests passed.
)

REM ---- 3. Commit --------------------------------------------------------------
echo.
echo [3/4] Staging and committing...
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
pause
endlocal
