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
git commit -m "Re-checked the deployed fixes against the live service. Four of the six held; the required-field gate did not, and the reason was the SAME defect one layer further out. Keying the frames by interface sheet instead of by dataset id was necessary and not sufficient, because two vocabularies are in play for the same sheet: the template names it after the Oracle interface table (POZ_SUPPLIERS_INT) and the analyst's curated required-field list names it after the workbook tab (Supplier Import). Neither side is wrong and they never matched, so the gate recognised no sheet at all and still reported every required field absent. Sheet aliases now live in the data file next to the list they resolve, both spellings the repo already uses are included, and matching folds case and punctuation. The second half is scope. The Supplier bundle spans six interface tables and one conversion's template declares one of them, so five of the six curated sheets are a sibling conversion's file. Demanding them here blocked a conversion for data it was never going to write. A curated sheet outside what this conversion owns is now reported as not applicable and never blocks, while a sheet the template DOES declare and did not produce still fails hard, which is the failure a gate exists to catch. build_sheet_frames records a declared-but-fieldless sheet as None rather than omitting it, so those two cases stay distinguishable. Consequently the post-mapping report no longer counts fields it did not check: checked is what this conversion owns, curated is the bundle total, not_owned is the difference, and the popup message states how many of the curated sheets were actually examined rather than implying all of them. Live, the Supplier conversion went from blocked with 23 phantom failures to not blocked, 1 of 6 sheets checked, which is the truthful answer. Also from the live re-check: the duplicate scan reported 183 rows scanned and 29 compared, which is true and reads as though 154 rows were skipped. They are rows whose name shares its opening characters with nothing else, so they have no candidate partner at all. That is now its own count, and the three buckets add up to the rows scanned so the number can be checked instead of trusted. Confirmed live after deploy: saved transformation rules load (was 500 on any conversion that had one), the report's required-field section populates (was zero on every conversion), learnings return their source system and sheet scope (200 of 200 now carry netsuite, previously none carried anything), and the duplicate scan reports its own coverage. 335 tests over 32 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
