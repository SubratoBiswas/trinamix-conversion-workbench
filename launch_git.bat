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
git commit -m "Two features that were deployed and inert on the existing data, found by the live run, plus the one-to-many picker. Column rules were mined correctly and reached nothing. The parser reads Oracle's header comments and mines 136 of 156 Supplier columns offline, but the template seeder skips a template whose business object already exists, so the FBDIField rows stored before the miner existed were never re-read. Live, a Supplier conversion reported 3 columns carrying a rule out of 156, and the db_column was empty on the one finding it did produce. Shipping a feature that only works on templates nobody has yet is not shipping it. The seeder now detects the condition from the data rather than a version flag — not one field carrying a db_column means the comments were never read — and enriches the stored fields in place rather than re-seeding, because the existing field ids are referenced by every mapping, rule and gold row on file and replacing them would orphan the lot. Two of the twelve supplier corrections went missing for a related reason. The seeder deduped on kind, target object, target field and client, and a field like Alternate Name legitimately carries several column_mapping rows, one per source column, so find-one-for-this-field matched an arbitrary alias and updated THAT instead of creating the correction. Alternate Name and Parent Supplier were the two that collided. A rule-correction now includes its own captured_from in the identity, so it owns its row and coexists with the aliases; at apply time the strongest-transform sort puts the rule first. The Split and combine modal had a visible defect: choosing Describe a rule left the Many-sources-to-one-field panel rendered above it, because the branch was written as split-or-everything-else. Selecting a tab now shows that tab only. And the one-to-many tab now has the picker the many-to-one tab has: a filterable tick-list of target fields instead of a stack of dropdowns behind an Add part button. Choosing four fields used to mean four clicks to add rows and four dropdowns to hunt through on a template with 1,365 fields, and the part indexes were typed by hand, which is where it went wrong most often — two rows silently sharing an index. Ticking assigns the indexes in order, so they cannot collide. Live test after the deploy: 22 checks, 16 pass, 2 fail, 1 partial, 3 not tested because proving them needs a write to production data. The required-field gate now returns not blocked with 1 of 6 curated sheets checked on the conversion that twice returned blocked with 23 phantom failures; saved rules load; learnings carry their source system; the duplicate scan's three row buckets add up to the rows scanned; and the column-rules endpoint's first live answer was a genuine finding, Supplier Number non-numeric on 183 of 183 rows. 458 tests over 38 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
