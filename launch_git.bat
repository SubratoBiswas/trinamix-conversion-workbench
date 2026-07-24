@echo off
cd /d "C:\Users\SubratoBiswas\trinamix-conversion-workbench"
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
echo Removing stray files from any previous multi-line-commit misparse...
del /f /q "Payee" 2>nul
del /f /q "Corporation)'" 2>nul
git rm --cached --ignore-unmatch "Payee" "Corporation)'" 2>nul
echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__
echo Staging...
git add -A
echo Committing (no-op if clean)...
git commit -m "Efficient merged Download-all for multi-source projects: generate every interface's merged file in the BACKGROUND then fast-zip. New POST conversions/project/{id}/generate-merged-all kicks off merged generation for every interface object (grouped by target_object, carrier = lowest load-order conversion), sets each carrier output_status=generating and runs generate_merged_artifact per object off-request; client polls /generation-status per carrier. GET download-all now REUSES the merged artifact already written under each object's carrier (ConvertedOutput latest, matching ext) and only zips - no inline rebuild (regenerate=true forces inline); returns 409 if nothing generated yet so the client generates first. Frontend OutputApi.downloadAll now orchestrates generateMergedAllAndWait (background generate + poll all carriers) then the fast reuse-zip, with onTick progress; new generateMergedAll/generateMergedAllAndWait helpers. ProjectOverview Download-all: phase 1 maps each source conversion (own mapping), phase 2 merges+generates one file per interface in background then downloads (was per-source generate + per-source zip). This keeps wide multi-source merges (Item 1365 / Customer 1254 cols) off the ~100s gateway timeout - the slow work is background, the download is a quick zip. Backend py_compile clean; 12 merge/layout unit tests pass; frontend builds (pre-existing tsc noise only). Single-line message on purpose."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
