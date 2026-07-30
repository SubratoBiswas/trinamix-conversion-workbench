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
git commit -m "Three separate reasons a rule change never reached the output file. Together they are why nothing an analyst changed appeared in a download, why a Fix reported success and changed nothing, and why one analyst's change was invisible to a colleague. One: the download served STALE artifacts. download-all reuses the newest file on disk when regenerate is false and never once consulted the status field, even though a stale variable was declared right there and every write path had been dutifully marking outputs stale for weeks. Staleness was recorded everywhere and read nowhere. A stale artifact is now treated exactly like a missing one — fall through and rebuild — and the single-conversion download refuses with an explanation instead of handing back a file the rules have moved on from. That also explains the two-laptop symptom precisely: there is one shared database and one shared output directory, so both analysts were served the same cached file. Two: saving a rule never marked anything stale in the first place. The rule endpoints — add, edit, delete — wrote to the database and left the artifact flagged fresh, so even a correct reuse check would have kept the old file. Every write that changes what generation would produce now marks the outputs stale: add, update and delete rule, and approve and update mapping. Three, and this is why Batch ID kept shipping 900001 and Supplier Name New kept populating even though both are seeded as blank: the analyst's correction files were not feeding the strategy overlay's BLANK set. A suppress_field learning reaches the mappings, but the control-default pass skips a column only if it is named in that blank set, and _CONTROL_DEFAULTS carries batch id equals 900001. A field with no mapping row at all — which is exactly what an unmapped Batch ID is — was therefore refilled on every generate, by design, invisibly. The 30-Jul corrections now feed the overlay, so batch id, supplier name new, inactive date, tax reporting name, procurement bu and liability distribution are all in the blank set and nothing downstream can refill them. Alongside those: a Keep blank button, because 'leave this column empty' had no way to be said. Clearing a fixed value only leaves the field OPEN, which is not the same thing — a control default or the AI refills it on the next generate, which is precisely how Batch ID kept coming back. The button now clears the source and the default, marks the mapping not_applicable so the generator suppresses it, blanks any duplicate row for the same target field (not_applicable ranks BELOW approved in the per-target dedup, so a stale sibling would have won and shipped the value while the UI said blank — the same bug one layer down), writes a suppress_field learning so every current and future conversion inherits the decision, and marks the built outputs stale because the file on disk still has the value in it. Tax Organization Type is CORPORATION and INDIVIDUAL in Oracle's own casing, in both the corrections file and the strategy defaults, with a test that fails if the two ever disagree. 481 tests over 40 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
