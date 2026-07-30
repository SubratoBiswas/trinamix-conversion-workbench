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
git commit -m "Column-rule enrichment was running on the wrong template, and Describe-a-rule now writes to several fields. The enrichment shipped last round and still reported 3 columns carrying a rule out of 156. The cause was one call: it resolved the template with find_one on the business object, and the live instance has THREE templates whose business object is Supplier — ApSuppliersImport, Supplier Import and SupplierImport — because the analyst has uploaded their own alongside the bundled copy. So it enriched an arbitrary one, which was not the one the conversion uses. It now enriches every stored copy of the interface, which is the right semantics anyway: the workbook's comments describe the INTERFACE, not one row in the template table. Field matching also falls back to the field name alone when the sheet names disagree, because an uploaded template may name its sheets differently while carrying the same columns. Describe a rule can now write to several target fields from one sentence. Splitting a phone into country, area, number and extension is one sentence, not four; the single dropdown forced the analyst to retype it once per destination and the copies then drifted apart as each was edited. The endpoint takes target_fields as a list and still accepts the old singular form, and plan_learnings fans out one learning per module per field. The many-sources direction needs nothing extra: name the columns in the sentence and the translator puts them in the rule's own config. The one-to-many tab got the same filterable tick-list the many-to-one tab has, replacing a stack of dropdowns behind an Add part button. Part indexes are assigned in tick order rather than typed, which is where the old form went wrong most often — two rows silently sharing an index. And selecting Describe a rule no longer leaves the Many-sources panel rendered above it; the branch had been written as split-or-everything-else. Re-run of the two live failures from the previous report: supplier corrections now seed 12 of 12, Alternate Name and Parent Supplier included, once the dedup key stopped matching an arbitrary alias for the same field. Column rules is fixed here but unverified live, because the fix is in this commit. 458 tests over 38 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
