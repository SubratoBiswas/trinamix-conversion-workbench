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
git commit -m "QA Open Issues fixes plus the mapping-tools wave. Supplier: User Account Action no longer forced to NONE (removed from control-defaults and the authoritative set so the Login Access value-map No-to-blank survives into the downloaded FBDI); Exchange Rate, Fax Area Code, Invoice Match Option and Enable B2B confirmed correct in the downloaded file. Duplicate mapping rows (a re-run race doubled rows, so a rejected field kept a stale suggested twin and a target repeated many times in canvas and CSV) are collapsed on read in list-mappings, healed physically at map time in run_mapping_suggestions, and cleanable via a new POST conversions dedupe-mappings endpoint; the strongest human status wins (overridden, approved, rejected, not_applicable, suggested). Engine: CONDITIONAL_DATE (both config schemas plus column-ref date tokens) and MAP_BOOLEAN implemented and registered in RULE_TYPES. Also includes the earlier mapping-tools wave: Mapping Documents review surface, manual template mapper, AI vet and fill helpers with persisted verdicts, clickable KPI and precedence-layer filters. Table view: fix Required-column sort doing nothing - the sort, count, filter, gap-check and CSV all read the template field flag f.required, but some templates (HDL) carry required only on the mapping row (target_required), so the key was falsy for every row and clicking Required reordered nothing; now a single unified per-row req = f.required OR m.target_required drives sort, count, Required-only filter, the row badge and the CSV, so they can never disagree. Single-line message on purpose. Full notes in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
