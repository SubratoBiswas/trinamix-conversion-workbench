@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM RUN FROM A COPY. This is not defensive tidiness -- it is required now that
REM this script is delivered as a patch and therefore PATCHES ITSELF.
REM
REM cmd.exe does not load a batch file into memory. It reads one command, seeks
REM back to a stored BYTE OFFSET, and reads the next. Rewrite the file while it
REM is running -- which is exactly what `git apply` on a patch touching this file
REM does -- and the next seek lands in the middle of a line. The remainder
REM executes as garbage: a syntax error, or worse, half a command. Any script
REM that can modify itself has to be running from somewhere else by then.
REM
REM So: copy to %TEMP%, re-run from there, and pass the real repo directory
REM through, because %~dp0 inside the copy points at %TEMP%.
REM ===========================================================================
if /i "%~1"=="__FROMCOPY__" goto :main
set "SELFCOPY=%TEMP%\launch_git_run.bat"
copy /y "%~f0" "!SELFCOPY!" 1>nul 2>nul
if not exist "!SELFCOPY!" (
  echo ****** STOPPED: could not copy this script to %TEMP%.
  echo        It must run from a copy -- a deploy that patches this file would
  echo        otherwise rewrite it mid-run and execute the remainder as garbage.
  pause
  exit /b 1
)
call "!SELFCOPY!" __FROMCOPY__ "%~dp0"
set "RC=!ERRORLEVEL!"
del /f /q "!SELFCOPY!" 1>nul 2>nul
exit /b !RC!

:main
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
REM The repo, handed over by the launcher above -- NOT %~dp0, which inside the
REM temp copy is %TEMP%.
cd /d "%~2"
if not exist ".git" (
  echo ****** STOPPED: no .git here, so this is not the repo root. ******
  echo        Looked in: %CD%
  pause
  exit /b 1
)

set "MSG=COMMIT_MSG.txt"
set "PATCHED="

REM ===========================================================================
REM MANIFEST - the patches THIS deploy expects, in the order they apply.
REM
REM Rewritten every time a deploy is prepared. It is here rather than in a
REM covering note because a note is a thing you have to have read: the script
REM checks the set is complete before it touches anything, so a patch that never
REM made it out of the download folder stops the deploy instead of shipping half
REM of it. Half a deploy is the worst outcome available -- it commits, it pushes,
REM it builds green, and the change you were deploying is not in it.
REM
REM ONE FILE. Earlier deploys handed over a numbered series, which meant seven
REM downloads and seven chances to miss one -- and missing one is exactly the
REM partial deploy this check exists to stop. Everything for a deploy is squashed
REM into deploy.patch now, so the set is complete or it is absent, with nothing
REM in between. An already-applied patch is skipped, so re-running is safe, and an
REM old numbered patch left in the folder is reported rather than applied.
REM ===========================================================================
REM Empty when the files were written straight into the repo folder rather than
REM handed over as a patch -- which is now the normal case, since the assistant
REM can write here directly. Name patches here only when there ARE patches.
set "EXPECT="

REM WHAT THIS DEPLOY CONTAINS. Rewritten every time, exactly like EXPECT.
REM
REM EXPECT going empty is now the normal case, because the assistant writes
REM into the repo directly. That removed the one line that used to say what a
REM run was shipping, so every deploy started looking identical to every other
REM deploy -- and a green run that shipped the wrong change is the failure this
REM script exists to make impossible. Named here, echoed at the start and again
REM on the DONE screen, so the thing you deployed is stated twice.
REM
REM No brackets or ampersands in the text: it is echoed inside an IF, where cmd
REM would parse them as syntax.
set "DEPLOY_NOTE=LATEST DATE WINS, now on BOTH write paths - so all five modules. There are only two writers in this tool: _transform_frame, which Supplier, Customer, Item and BOM all use, and the HDL writer, which Employee uses. The same test was wrong in FOUR places and every one of them only counted a bound source column, so a fixed value the analyst typed was invisible: the strategy-overlay guard, the transformation-rule branch, the control-defaults explicit set, and the HDL writer which had no date test at all. All four now rank by date - an approved mapping or fixed value beats anything older and loses to anything newer, undated loses to dated, suggested is not a statement. No per-object copies remain. Suite 1280 passing, 13 skipped, 0 failing. REGENERATE everything."

echo Checking the deploy set...
set "MISSING="
if not defined EXPECT goto :setok
for %%E in (%EXPECT%) do (
  if not exist "%%E" (
    if defined MISSING (set "MISSING=!MISSING! %%E") else (set "MISSING=%%E")
  )
)
if defined MISSING (
  echo.
  echo ****** STOPPED: this deploy is incomplete. Nothing has been changed.
  echo.
  echo   Missing from %CD%:
  for %%M in (!MISSING!) do echo     - %%M
  echo.
  echo   Every file listed above has to be in this folder. Deploying a partial
  echo   set commits, pushes and builds green with your change not in it, which
  echo   is far harder to notice than a script that refused to start.
  echo.
  pause
  exit /b 1
)
echo       every patch this deploy expects is present.
:setok

REM A .patch left over from an earlier deploy is not an error -- it will be
REM recognised as already applied and skipped -- but it should be SAID, because
REM an unexpected file is also what a wrongly-named new patch looks like.
for /f "delims=" %%P in ('dir /b /o:n *.patch 2^>nul') do (
  echo %EXPECT% | find "%%P" 1>nul 2>nul
  if errorlevel 1 echo       note: %%P is not part of this deploy - expect it to be skipped.
)


REM NO PAGER. `git diff --cached --name-only` on a long list opens `less`, which
REM stops the script dead at a `:` prompt with no explanation -- it looks exactly
REM like the deploy finished, and it has not even committed yet. A batch script
REM has no business opening an interactive pager.
set "GIT_PAGER=cat"
set "PAGER=cat"

echo.
echo ===================== DEPLOY =====================
echo   Repo: %CD%
if defined DEPLOY_NOTE echo   Shipping: %DEPLOY_NOTE%
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

REM The deploy's OWN scratch files. `git add -A` swept deploy.patch, COMMIT_MSG.txt
REM and a renamed supplier-site-tab.patch_old into the 03-Aug commit -- 170 KB of
REM patch text stored permanently in history, and it would repeat every deploy,
REM since the next deploy.patch reads as a modification of the tracked one. They
REM are the INPUT to a deploy, not part of the source. Untracked here and ignored
REM from now on; the files stay on disk, so re-running is unaffected.
echo Untracking the deploy's own scratch files...
git rm -r --cached --ignore-unmatch "*.patch" "*.patch_old" "COMMIT_MSG.txt" 1>nul 2>nul
findstr /x /c:"*.patch" ".gitignore" 1>nul 2>nul || (
  echo.>>".gitignore"
  echo # Deploy inputs - see launch_git.bat. Not source.>>".gitignore"
  echo *.patch>>".gitignore"
  echo *.patch_old>>".gitignore"
  echo COMMIT_MSG.txt>>".gitignore"
)

echo Removing scratch files (QA renders, Excel lock files)...
rmdir /s /q "_qa" 2>nul
del /f /q "~$*.xlsx" "~$*.pptx" 2>nul
git rm -r --cached --ignore-unmatch _qa 1>nul 2>nul

REM ---- 1. Apply any pending patch ---------------------------------------------
REM --check first, so re-running this script is harmless: a patch already in the
REM tree would otherwise fail to apply and look like a real error.
REM
REM --ignore-whitespace is REQUIRED here, not a nicety. This working tree holds
REM CRLF line endings; patches are cut on Linux with LF. git apply matches
REM context byte for byte, so every hunk failed with "error: while searching
REM for:" against a line that was, to the eye, identical -- the only difference
REM being a trailing CR. It reads as a diverged tree and is nothing of the sort.
REM The flag treats the CR as the whitespace it is.
echo.
echo [1/3] Pending patches...

REM ORDER MATTERS, so the order is FORCED.
REM
REM `for %%P in (*.patch)` walks the directory in whatever order the filesystem
REM hands back. On NTFS that is usually alphabetical and it is not promised to
REM be. Patches are cut in sequence and later ones are built on earlier ones --
REM 02 touches files 01 has already changed -- so a run that applied 02 first
REM would fail with "error: while searching for:" against a tree that is
REM perfectly fine, and read as a diverged repo. `dir /b /o:n` sorts by name,
REM which is why the patches are numbered 01, 02, 03 ...
set "FOUND="
for /f "delims=" %%P in ('dir /b /o:n *.patch 2^>nul') do (
  set "FOUND=1"
  echo       found: %%P
)
if not defined FOUND (
  echo       none found - nothing to apply.
) else (
  echo       applying in the order listed above.
  echo.
)

for /f "delims=" %%P in ('dir /b /o:n *.patch 2^>nul') do (
  git apply --check --ignore-whitespace "%%P" 1>nul 2>nul
  if errorlevel 1 (
    git apply --reverse --check --ignore-whitespace "%%P" 1>nul 2>nul
    if errorlevel 1 (
      echo.
      echo ****** STOPPED: %%P neither applies cleanly nor is fully applied.
      if defined PATCHED (
        echo ****** Earlier patches in this run ARE applied to your working tree:
        echo ******   !PATCHED!
        echo ****** Nothing has been committed or pushed.
      ) else (
        echo ****** Nothing has been changed, committed or pushed.
      )
      echo.
      REM Say WHICH file and why. "Your tree has diverged" is true of a genuine
      REM conflict and equally true of a patch whose first half you already
      REM deployed -- and those need opposite responses. Printing the real
      REM reason is the difference between a five-second fix and an hour.
      echo ---- what git actually objected to ----
      git apply --check -v --ignore-whitespace "%%P" 2>&1
      echo ---------------------------------------
      echo.
      echo If it says "already exists in working directory", this patch is
      echo PARTLY applied - you deployed an earlier one that overlaps it. Ask
      echo for a patch of only what is still missing; do not force this one.
      echo.
      echo Otherwise your tree really has moved on from the baseline it was cut
      echo against.  Try:  git apply --3way --ignore-whitespace %%P
      pause
      exit /b 1
    ) else (
      echo       %%P - already applied, skipping.
    )
  ) else (
    git apply --ignore-whitespace "%%P"
    if errorlevel 1 (
      echo ****** STOPPED: git apply failed on %%P. Nothing committed. ******
      pause
      exit /b 1
    )
    echo       %%P - applied.
    REM Name the ones this run actually touched. The old version printed a
    REM revert line for EVERY .patch in the folder, including the ones it had
    REM skipped as already-applied -- and reverting one of those undoes a change
    REM that is already live.
    if defined PATCHED (set "PATCHED=!PATCHED! %%P") else (set "PATCHED=%%P")
  )
)

REM ---- 2. Commit --------------------------------------------------------------
echo.
echo [2/3] Staging and committing...

REM WHERE WE STARTED. Everything below compares against this.
REM
REM The old verify asked "is anything STILL STAGED?" and treated a clean index as
REM success. But a run with no patches in the folder also leaves a clean index --
REM there was nothing to stage in the first place -- so "committed nothing because
REM there was nothing to commit" and "committed successfully" printed the SAME
REM thing, and the script went on to report DONE and a pushed commit for a deploy
REM that never happened. Observed: a run that skipped one already-applied patch,
REM said "nothing to commit, working tree clean", said "Everything up-to-date",
REM and then said "DONE - committed and pushed".
REM
REM That is the exact shape this script exists to prevent, and it was in the
REM script. A moved HEAD is the only honest proof.
for /f %%i in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%i"

git add -A

echo.
echo ---- Staged for this commit ----
git --no-pager diff --cached --name-only
echo --------------------------------

git --no-pager commit -F "%MSG%"

REM VERIFY, on the only evidence that cannot lie: did HEAD move?
for /f %%i in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%i"
if "!BEFORE!"=="!AFTER!" (
  echo.
  echo ****** STOPPED: NOTHING WAS DEPLOYED. No commit was created.
  echo.
  echo   The working tree had no changes to commit. Almost always this means the
  echo   patch files for this deploy are not in this folder:
  echo.
  for %%E in (%EXPECT%) do echo     - %%E
  echo.
  echo   Put every one of them here, beside this script, and run it again.
  echo   ^(A patch that is already applied is skipped, so re-running is safe.^)
  echo.
  echo   Nothing has been pushed. This is deliberately NOT reported as success:
  echo   an earlier version of this script printed "DONE - committed and pushed"
  echo   on a run exactly like this one, and the deploy silently never happened.
  echo.
  pause
  exit /b 1
)

REM Belt and braces: anything still staged means the commit itself failed.
git --no-pager diff --cached --quiet
if errorlevel 1 (
  echo.
  echo ****** STOPPED: changes are STILL STAGED, so the commit did not happen.
  echo ****** Nothing has been pushed. Read the git error above.
  if defined PATCHED (
    echo ****** These patches ARE applied in your working tree:
    echo ******   !PATCHED!
    echo ****** Revert them ^(newest first^) with:
    echo ******   git apply --reverse --ignore-whitespace ^<name^>
  )
  pause
  exit /b 1
)
echo       committed - HEAD moved from !BEFORE:~0,7! to !AFTER:~0,7!.

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
if defined DEPLOY_NOTE echo   Shipped       : !DEPLOY_NOTE!
if defined PATCHED echo   Patches applied: !PATCHED!
echo   Pushed commit : %SHA%
echo   Now check     : https://trinamix-conversion-backend.onrender.com/api/health
echo   Deployed when the "commit" field there starts with %SHA%
echo   (Render takes a few minutes. The free tier cold-starts, so the first
echo    request after idle takes about 45 seconds - that is not your bug.)
echo.
echo   Two Render dashboard settings. Neither is fixed by any deploy:
echo.
echo     1. SECURITY - Backend -^> Environment -^> JWT_SECRET.
echo        The manifest used to set SECRET_KEY, which nothing reads, so the
echo        live backend signs every token with the default that is committed
echo        to this repo. generateValue only fires when a variable is CREATED,
echo        so the rename in render.yaml does not repair a service that exists.
echo        Setting it signs everyone out - that is the correct outcome.
echo.
echo     2. Static site -^> Redirects/Rewrites -^> source /*  destination /index.html
echo        action Rewrite.  Without it, refreshing or pasting a deep link
echo        returns 404 with a blank body, which looks just like a broken page.
echo        Measured 05-Aug - tx-conversion-workbench.onrender.com/clients 404s.
echo.
pause
endlocal
