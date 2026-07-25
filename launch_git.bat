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
git commit -m "AI data-intelligence: fuzzy entity resolution + source anomaly detection. (1) Fuzzy duplicate/entity resolution (entity_resolution.py) - finds records that are the SAME entity despite non-identical keys/names ('Acme Inc' vs 'ACME, Incorporated'), which exact-key merge_dedupe can't catch: per-object identity-field detection, blocking + token/difflib similarity + union-find clustering with confidence and field-level evidence; optional AI adjudication of borderline clusters (fallback deterministic). GET conversions/{id}/duplicate-candidates over the merged frame; Output Preview 'Duplicate suspects' tab (with 'Adjudicate with AI'). (2) Source-data anomaly/outlier detection (anomaly_service.py) - profiles a source extract BEFORE mapping and flags high-null, leading/trailing spaces, mixed types, numeric outliers (IQR), embedded units, casing/whitespace variants, non-printables and duplicate rows, each with severity/count/examples; optional AI risk notes. GET datasets/{id}/anomalies; Dataset detail 'Anomalies' tab. Both services dependency-light and unit-tested (7 + 10 tests). Note: source-profiling target-module recommendation (list item 4) already exists via datasets /classify + /suggest-template. Backend py_compile clean; frontend builds (pre-existing tsc noise only). Single-line message on purpose. --- PRIOR (also in this push): Add filled-in Oracle FBDI Excel template output (alongside the CSV bundle) for Supplier/BOM/Customer/Item. New template_fill_service opens the REAL bundled Oracle workbook (keep_vba, macros/instructions preserved), detects each interface sheet's header + first data row using the SAME logic as fbdi_parser (tabular: title/'* Required'/header row 4/data row 5 from col A - Supplier/BOM/Customer; Oracle-transposed: col-A label column Name/Description/Data Type/Technical Name, headers on the Name row from col B, data below the metadata block - Item), wipes the shipped SAMPLE rows, and writes the finalized per-sheet frames into the matching columns by normalised header. output_service gains fmt='template': materializes the template file (disk or rehydrated from Mongo FBDITemplateFile), builds per-sheet finalized frames (no supplier reorder/END - the template owns column order), fills the workbook, saves as .xlsm named after the Oracle template; degrades to a fresh xlsx if no source file. fmt='template' flows through generate-output, generate-merged, generate-merged-all and download-all (reuse family: template->{xlsm,xlsx}); query patterns widened to csv|xlsx|template. Frontend: per-conversion 'Excel' button + 'Download all (Excel templates)' / 'Filled Excel templates (.zip)' that run the same background-merge-then-zip path (OutputApi fmt widened to include 'template'); download() already saves the server's real .xlsm name. Employee stays HDL (.dat) - no Excel FBDI template exists for it. Backend py_compile clean; 16 unit tests pass (4 new template-fill: supplier tabular row-5 placement, item transposed row-9/col-B placement, sample-clear, unmatched-column safety, instructions-sheet untouched). Minor: openpyxl drops a decorative WMF image on save (macros intact). Single-line message on purpose."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
