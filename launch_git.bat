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
git commit -m "AI differentiators wave 1 on top of multi-source + DQ + rule store. Multi-source: Conversion.dataset_ids (priority order) with dataset_id mirrored; generate-set + PUT conversions/{id}/sources accept multiple datasets; each source converted individually then converged + de-duplicated (master objects dedupe on natural key with source priority, child interfaces exact-row); per-source preview endpoint; single-source path unchanged. AI SURVIVORSHIP: merge_dedupe builds a golden record per key - each field takes the first non-blank value across sources in priority order (top source wins, blanks back-filled from lower sources); default-on in generation; unit-tested. Generate-time DQ (generate_dq): cleanse the merged frame (trim + custom cleansing rules) then validate (built-in FBDI checks + custom rules); advisory dq_report attached to the artifact and shown in the download toast. RULE STORE (DataQualityRule scoped by object+client): create via EXTRACT (from template), UPLOAD (xlsx/csv/json), MANUAL, plus CSV export, on a new Validation and Cleansing page. AI-DRAFTED RULES: POST /dq-rules/ai-propose (Claude proposes validation+cleansing rules from field metadata + data sample; deterministic fallback) reviewed and saved via /dq-rules/bulk; AI suggest button + review panel on the page. PREDICTIVE PRE-LOAD: GET /conversions/{id}/preload-report validates the merged frame without writing a file and returns plain-English what-Oracle-will-reject-and-how-to-fix guidance. LOAD REMEDIATION + RECONCILIATION: POST /load-runs/{id}/explain-errors fills root cause+fix per LoadError; GET /conversions/{id}/reconciliation reports source vs merged-output vs load counts with a narrative. FIX: register DataQualityRule in the Beanie document_models list in database.py (it was imported but not registered, so its collection was not initialized - the generate-time DQ, preload-report and rule CRUD/extract hit CollectionWasNotInitialized; now works). FIX: ai-propose was silently falling back because Claude's JSON was truncated at max_tokens=4000 (JSONDecodeError); raised to 8000, capped the field catalog to 60, and added a salvage parser that keeps the complete proposals even if the tail is cut, plus a _debug block on the response. FIX: 'Fill blanks with AI' (ai-fill-blanks) was crashing the worker (ERR_FAILED/CORS on the client) - _sources_for_conversion was loading the full wide source file synchronously on the request thread; now it materialises from GridFS, caps to 5000 rows and runs off-thread, and the candidate-ranking loop is moved off the event loop (asyncio.to_thread) with the batch capped at 40. Epics roadmap in docs/AI_Differentiators_Roadmap.md. Tests: backend/tests/test_multisource_dq.py + supplier layout suite pass; backend py_compile clean; frontend tsc no new errors. Single-line message on purpose. Full notes in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
