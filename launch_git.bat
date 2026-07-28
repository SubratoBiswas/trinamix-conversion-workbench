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
git commit -m "Header row is now decided by output format: filled Excel templates keep their column labels and FBDI CSV bundles are headerless, which is what the Oracle loader expects. Previously only supplier CSVs were headerless, so every other object shipped a CSV with a header row that Oracle rejects as a data record. Added a Header Auto/On/Off toggle on the Conversion Objects card covering both zip downloads. Vet options with AI and Export mapping (Excel) are now available in Canvas view, not just Table. Per-interface-sheet source routing: one conversion now produces one FBDI bundle even when the input spreads the object across several worksheets. Customer tab feeds the party, account and contact sheets; Address tab feeds the address sheets. The mapper now sees the union of columns across all bound sources. Plus the QA sheet of 27-07: fixes issues 1, 5, 6, 7 and 8. Address Name (and Supplier Site, Pay, Ordering, RFQ) no longer overwritten by the PRIMARY control constant when the analyst explicitly mapped the field. Deleted learnings now tombstone so seeds, auto-capture and gold re-uploads cannot resurrect them, with restore and retired-list endpoints. Default-only mappings are learned on save instead of only after a generate. Two-sheet workbooks expand into one dataset per sheet on the Convert-a-file path. Saved custom transformation rules load back into the author modal and edit in place. Detail in docs/SESSION_HANDOFF.md sections 9.13 to 9.15."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
