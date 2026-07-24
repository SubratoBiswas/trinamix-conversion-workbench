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
git commit -m "Fix merged Download-all 409 (files generated but zip empty): the reuse lookup required output_file_name to end in .csv, but a csv-family generation is often a .zip (Oracle FBDI bundle) or per-object zip, so every interface was treated as not-generated and download-all returned 409 after the background generate reported 6/6. Download-all now scans EVERY conversion in the interface group (not just a re-derived carrier, which could differ by sort tie-break) and reuses the newest ConvertedOutput whose file exists, rejecting only a true format mismatch (xlsx vs csv/zip family) instead of an exact-extension match. Part of the efficient merged Download-all: POST conversions/project/{id}/generate-merged-all builds every interface's merged file in the BACKGROUND (per-object generate_merged_artifact off-request, carrier output_status polled via /generation-status); GET download-all then just fast-zips the already-generated files (regenerate=true forces inline rebuild). Frontend OutputApi.downloadAll orchestrates generateMergedAllAndWait then the reuse-zip with onTick progress; ProjectOverview Download-all maps each source (own mapping) then merges+generates one file per interface in background. Keeps wide multi-source merges (Item 1365 / Customer 1254 cols) off the ~100s gateway timeout. Backend py_compile clean; 12 merge/layout unit tests pass. Single-line message on purpose."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
