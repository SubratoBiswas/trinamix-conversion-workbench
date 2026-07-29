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
git commit -m "Mapping-document governance, source-system detection, required-field gate and a post-mapping report. Only the newest mapping document governs a client and module: applying a second file for the same module left the first silently in force, so every mapping v1 asserted that v2 does not mention kept applying and generated files kept the v1 rules with nothing on screen saying so. Older documents now become superseded, learnings they asserted that the new file does not re-state are tombstoned (soft, so restore still works), and affected outputs are marked stale rather than deleted. The retire set is old pairs minus newly asserted pairs, never simply every old pair, because a pair present in both was already updated in place and retiring it would delete the value that just replaced it. Source system is now detected per sheet instead of typed into the upload form: an explicit source-system column wins, then the source column's own header, then any other header naming a system, then the sheet and file names, and only then the model. That is what catches workbooks carrying several legacy systems side by side, which one dropdown cannot describe. Headers naming the target are refused so mappings cannot be imported backwards, with a carve-out for Oracle EBS which names Oracle but is a real source, and two-letter aliases must match a whole header so ns inside Transactions is not NetSuite. The uploader's choice still wins but a disagreement with the file is reported. Supplier required fields seeded from the analyst list of 29-Jul and enforced as a hard gate: the check runs on the finished frames rather than the mappings, because a field can be mapped to a column that exists but is empty, mapped to a column absent from the extract, or satisfied by a control default with no mapping at all. Absent or wholly empty blocks generation with a popup naming every sheet and field, since Oracle rejects every row and producing the file only moves the failure to cutover. Partially filled is reported but does not block. New post-mapping report rolls up coverage by layer, validation and cleansing pass or fail, rules applied, and required fields missing, including an unattested count of fields resolved by the matcher or AI alone. 31 new tests; 23 test files run in full."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
