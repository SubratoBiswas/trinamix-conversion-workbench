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
git commit -m "A tombstone guard that could not see tombstones, two silences at generate, and the 30-Jul supplier mapping. learning_service._upsert is the path every interactive save runs through: approving a mapping, adding a fixed value, saving a rule. It queried the library with LearnedMapping.find, which injects is_deleted not-equal-true, so a retired row was invisible; the is_deleted check written just below it could therefore never fire, the does-this-already-exist loop found nothing, and the function fell through to INSERT A FRESH DUPLICATE. A learning the analyst had deleted came back on the next approve that touched the field. That is the third instance of CW #5, after auto-capture and the seeds, and the first one on the interactive path. An AST audit over all 47 LearnedMapping query sites in 18 modules found three functions that reason about is_deleted while filtering it out. Only one was a real defect: the two in catalog seeding and mapping supersession are correct, because there EXCLUDING an already-retired row is the intent, and each is now recorded as an allowed exception with its reason so the list stays honest rather than growing silently. The audit is kept as a test, since it is what found the bug. Two silences removed at generate. apply_learned_to_conversion was wrapped in a bare except pass: if applying the library threw, the file was generated with NO learnings applied and nothing anywhere said so, which reads exactly like approvals not being saved. capture_learnings_from_conversion had the same bare pass, so a failure meant nothing was learned from a completed conversion and the analyst was never told. Both now log with the exception, the apply failure is recorded on the artifact's own DQ report so the reason travels with the file instead of living in a server log nobody reads, and the count of applied learnings is reported alongside it. Same shape as the required-field section that silently read zero for weeks. The 30-Jul supplier mapping workbook is seeded from the GREEN rows only, which is the workbook's own legend for Mapped. Green is read from the cell fill rather than by eye. The other colours mean Questions to NextPower, Duplicate, Oracle required but missing, and Not to bring, and none of those is an instruction to map anything, so none is imported. Of 128 green rows, 34 target DFF or standard rather than a named Oracle field and are excluded, because a descriptive flexfield is a decision about where a value belongs and not a mapping the engine can apply. That leaves 94, 54 NetSuite and 40 SyteLine, keyed by source system so the two systems' mappings for one field stay two rows rather than one overwriting the other. Eight target fields are fed by more than one green source column for the same system, including Taxpayer ID from three; both are seeded and the strongest-transform rule wins at apply time, but which column should govern is a business decision and is carried as an open question rather than settled quietly. 439 tests over 37 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
