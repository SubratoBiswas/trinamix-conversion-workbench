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
git commit -m "Multi-source-per-module + generate-time data quality + validation/cleansing rule store. A module (Item/BOM/Supplier/Customer/Employee) can now accept SEVERAL source files: Conversion gains dataset_ids (priority order) with dataset_id mirrored to the first for back-compat (models/conversion.py + schema + source_dataset_ids helper); generate-set and a new PUT conversions/{id}/sources accept multiple datasets; object_fanout binds every fanned object to all sources. Generation converts each source individually then converges + de-duplicates in output_service.build_converted_dataframe: master objects (unique key per source, from REFERENCE_KEY_FIELDS) dedupe on the natural key with SOURCE PRIORITY (first source wins), child interfaces (many rows per entity) fall back to exact-row de-dup; single-source path unchanged. Extracted merge logic to app/services/merge_dedupe.py. Per-source preview endpoint output-preview-by-source + OutputApi.previewBySource. Generate-time DQ (app/services/generate_dq.py): cleanse the merged frame (universal trim + custom cleansing rules) then validate (built-in FBDI checks + custom validation rules); an advisory dq_report (cleansing fixes, error/warning counts, hard-error block flag) is attached to ConvertedOutput and surfaced via generation-status and the Mapping Review download toast. Validation/cleansing rule store (models/dq_rule.py DataQualityRule scoped by object+client; routers/dq_rules.py; services/dq_rule_service.py): create rules via EXTRACT (derive required/max-length/value-set/numeric from an FBDI template), UPLOAD (xlsx/csv/json), or MANUAL, plus CSV export; rules auto-apply at Generate and are managed on a new Validation & Cleansing page (frontend). Unit tests: backend/tests/test_multisource_dq.py (merge/dedup priority, child-interface distinct rows, cleansing, validation report block) + existing supplier layout tests all pass. Backend py_compile clean; frontend tsc no new errors. Single-line message on purpose. Full notes in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
