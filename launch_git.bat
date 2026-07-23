@echo off
cd /d "C:\Users\SubratoBiswas\trinamix-conversion-workbench"
echo Cleaning any git locks...
del /f /q ".git\index.lock" 2>nul
del /f /q ".git\HEAD.lock" 2>nul
echo Removing stray files from the previous multi-line-commit misparse...
del /f /q "Payee" 2>nul
del /f /q "Corporation)'" 2>nul
git rm --cached --ignore-unmatch "Payee" "Corporation)'" 2>nul
echo Dropping tracked bytecode...
git rm -r --cached --ignore-unmatch backend/app/__pycache__ backend/app/services/__pycache__ backend/app/parsers/__pycache__
echo Staging...
git add -A
echo Committing (no-op if clean)...
git commit -m "AI vetting now also drives the mapping-detail recommendations, not just the table. The Alternative source columns panel in the inspector fetched its own candidates and never saw the toolbar vet, so its recommendations stayed unranked. It now honours the semantic guard (implausible options struck through, flagged, demoted, with the caution shown) even before AI, and has its own 'Vet with AI' button that reviews just that one field (only_uncertain false, so every option gets a verdict - cheap for one field), folds the verdicts in, re-sorts wrong/unlikely to the bottom, and shows the AI reason. So opening a field and clicking Vet updates that field's recommendations in place. Destination-side alternatives (rank target fields for a clicked source) is a separate reverse-ranking feature not yet built - flagged for follow-up. Also carries the prior fix: Vet-options-with-AI failing on wide templates plus clean up stray files. The button failed on the 1254-field Customer template because only_uncertain matched almost every unmapped field, sending hundreds of pairs to the model in dozens of sequential batches and blowing the ~100s gateway; a POST is not auto-retried so it fell straight to unavailable. Now the AI pass is capped at 60 pairs, prioritised implausible-first then lowest-confidence, and scoped by target_field_ids to the rows on screen (the UI sends the visible ids, max 120); the client also retries once on a cold-start 502/503/504. Also removes the empty Payee and Corporation files from the earlier multi-line-commit misparse. Single-line message on purpose. Full notes in SESSION_HANDOFF.md."
echo Pushing...
git push origin main
echo.
echo ====== DONE - Press any key to close ======
pause
