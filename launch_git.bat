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
git commit -m "Merge-by-interface output for multi-source projects (many sources -> one merged file per interface). When a project has several source files for the same interface (e.g. eBOS + NetSuite supplier), each is a separate conversion with its OWN mapping (heterogeneous schemas map correctly); the outputs are now merged into ONE de-duplicated, cleansed, validated Oracle file per interface instead of one file per source. Implementation: generate_output_artifact accepts an optional pre-built merged_df (skips its own source build); new build_merged_frame_for_object converts every bound conversion for a (project, target_object) with its own mapping and converges them via survivorship de-dup (first non-blank per field, source priority); generate_merged_artifact writes that merged frame once under a carrier conversion. Download all FBDI now groups conversions by interface object and emits one merged file each (was one per source - the 12-file zip becomes 6). New endpoints GET conversions/{id}/merged-preview (preview the merged interface result across all sources) and POST conversions/{id}/generate-merged; Mapping Review 'Generate & download' now produces the merged file (falls back to plain generate on error). Reuses merge_dedupe survivorship + generate_dq cleanse/validate + supplier layout/naming. EFFICIENCY: survivorship de-dup is now vectorised (blank->NaN then groupby.first, one pass) instead of a per-column Python agg - critical for wide objects (Item 1365 / Customer 1254 cols); generate-merged runs in the BACKGROUND (returns the carrier conversion id, client polls /generation-status) so wide multi-source merges never hit the gateway timeout; Mapping Review Generate & download uses generateMergedAndWait (poll) and falls back to plain generate. Backend py_compile clean; merge unit tests pass (incl. heterogeneous per-source frames); frontend tsc no new errors. Single-line message on purpose. Full notes in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
