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
git commit -m "Duplicate decisions are now per-row, and cleansing actually cleanses. Row keys hashed only the identity columns, so nine CRRC YANGTZE supplier rows differing solely by trailing punctuation collapsed to three keys: selecting one survivor lit up three rows, and Keep survivor only could not express WHICH physical row to keep, so apply_decisions fell back to whichever sat first in the frame. That fallback is frame-order dependent and could pick a different row at generation than the one reviewed. Keys now carry a content hash plus an occurrence ordinal, still derived purely from the data so they survive reordering; cluster_key reduces to identity parts first, so no saved decision and no cross-conversion keep_all learning is invalidated, and identity-only keys from before the change still resolve. The review list also never showed the effect of a verdict: rows a decision drops are now struck through and the cluster header projects the surviving count as soon as a survivor is picked. Separately, the scan returns at most max_clusters of 100 while cluster_count reported the true total, so a 343-cluster scan left 243 groups unreachable and the decided and undecided counters silently described only the visible slice; max_clusters is now a query parameter and the hidden count is stated on screen. New cleansing_rules service with four families: whitespace and edge punctuation, unicode and control-character normalisation, smart title case, and legal suffix standardisation, each switchable per field and previewable before being enabled. It replaces the bare whitespace trim in apply_cleansing and runs after duplicate decisions, so only rows that ship get cleansed. The two families that rewrite business values are off by default and labelled as such on the control, because Acme Limited becoming Acme Ltd changes a legal name in a client-facing FBDI. Numeric values, blanks and existing acronyms are guarded. Three new endpoints: GET and PUT conversions/{id}/cleansing-profile and GET conversions/{id}/cleansing-preview. 25 new tests pass under the pinned pandas 2.2.3 and under pandas 3, replayed end to end on a 5831-row supplier frame: the nominated twin survives, 9 rows become 1, cleansing then strips every trailing dot and non-breaking space without changing the row count."
echo Pushing...
git push origin main
echo.
echo ====== DONE - review the messages above, then press any key to close ======
pause
