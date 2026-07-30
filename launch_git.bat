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
git commit -m "The column-rule findings are now actionable from the screen that reports them, and the enrichment reaches every copy of a template. Confirmed live first: the Cleansing tab now reads 136 column rules from the template's own comments on the NetSuite supplier conversion, with the Oracle column names resolved — END_DATE_ACTIVE, TAX_COUNTRY_CODE, TAX_REPORTING_NAME — and three real findings: every one of 3,872 rows has a date outside YYYY/MM/DD, 19 Taxpayer Country values exceed VARCHAR2(2), and 10 Tax Reporting Names exceed VARCHAR2(80). Previously it found three rules out of 156 because the enrichment resolved the template with find_one on the business object and this instance has three templates whose business object is Supplier; it now enriches every stored copy of the interface, which is the right semantics anyway since the workbook's comments describe the interface rather than one row in a table. Each finding now carries a Fix button that builds the rule and saves it: a date becomes a DATE_FORMAT into the mask the TEMPLATE states rather than a hardcoded one, an over-long value becomes a SUBSTRING to the column's own limit, a non-numeric column gets its characters stripped keeping the sign and decimal point, an over-scaled number gets NUMBER_FORMAT at the stated places, and a column Oracle says is not used is blanked. The rule is captured as a learning too, per the standing instruction that a correction made once reaches every current and future conversion, and the output is marked stale rather than silently regenerated. Three findings deliberately get NO button, and each says why on the row instead. Which accepted code a wrong value should become is a business decision and belongs in the crosswalk. A number too big for its column is nearly always a mis-mapped source, so truncating the digits would produce a plausible wrong value and destroy the only evidence of the real problem. And nothing can invent a missing mandatory value. A one-click fix that did any of those quietly would turn a visible problem into an invisible one, which is the exact failure this panel exists to prevent — so the refusals are tested as carefully as the fixes. Describe a rule also now writes to several target fields from one sentence: splitting a phone into country, area, number and extension is one sentence, not four, and the single dropdown forced the analyst to retype it per destination so the copies then drifted apart. The endpoint takes a list and still accepts the singular form. 476 tests over 39 files, run on the pinned pandas 2.2.3 and on 3.0.2."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
