@echo off
cd /d "C:\Users\SubratoBiswas\trinamix-conversion-workbench"
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__ 1>nul 2>nul
echo Removing scratch files (QA renders, Excel lock files)...
rmdir /s /q "_qa" 2>nul
del /f /q "~$*.xlsx" "~$*.pptx" 2>nul
git rm -r --cached --ignore-unmatch _qa 1>nul 2>nul
echo Staging...
git add -A
echo Committing (no-op if nothing changed)...
git commit -m "Four defects found by running the deployed service instead of reading it. Each one was already marked fixed on the strength of unit tests that passed, and each sat on a seam the tests never crossed. The required-field gate blocked every conversion. The router handed the checker a dict keyed by dataset id, because that is what build_converted_dataframe's collect_frames populates, so no interface sheet name ever matched, every required field read as absent and blocked came back true on healthy data. A gate that always fires is worse than no gate, because it gets switched off. Per-sheet frames are now built by build_sheet_frames, which routes each sheet to the source file that supplies its columns and applies the same control defaults generation applies, so a field satisfied only by a curated constant passes. The post-mapping report's required-field section had never worked. It called the endpoint FUNCTION, whose max_rows default is a FastAPI Query object rather than an int, so the call raised inside pandas, a bare except swallowed it and the section read zero on every conversion. Live, include_required=true and =false returned byte-identical output. The gate is now a plain helper both paths call, and a failure records its reason instead of reporting a clean pass. GET conversions/{id}/rules returned 500 for any conversion that actually had a rule. TransformationRule stores target_field_id as an ObjectId, the response schema types it str, Pydantic will not coerce, and the endpoint stringified only the two ids the author was thinking of. The analyst's saved rule was intact in the database and unreachable in the UI, which is exactly the 27-Jul complaint that a saved rule disappears. Fixed at the call site and, so the next reference field cannot repeat it, once for every response schema via ApiOut. Oracle date coercion never ran on the EBS path. Frame headers were compared to template field names case- and punctuation-sensitively, so EFFECTIVE_START_DATE never matched EffectiveStartDate; and the accepted-format list omitted YYYY-MM-DD HH:MM:SS, the spelling every SQL and ODBC export writes. Either one ships 2020-01-15 to a loader that accepts only 20200115 and Oracle rejects every dated row. tests/test_ebs_output.py had asserted this from the start and had been failing in the repository; I had reported the suite as green without having run it. Also: the duplicate scan silently dropped any name group larger than the pair limit, so on a large extract no duplicates found and thousands of rows were never examined produced identical output. Oversized groups now fall back to sorted-neighbourhood matching and the result states what was compared how. And tests/test_app.py, written for a SQLite era that no longer exists, now skips with a reason instead of contributing eleven red failures that hid real ones. 325 tests over 30 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
