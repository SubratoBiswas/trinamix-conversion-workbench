@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_p2s3 started %date% %time% ===== > push_p2s3.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> push_p2s3.log 2>&1
echo --- staging ALL of backend/app + backend/tests (partial-proof) --- >> push_p2s3.log 2>&1
git add backend/app backend/tests >> push_p2s3.log 2>&1
echo --- staged files --- >> push_p2s3.log 2>&1
git status --porcelain >> push_p2s3.log 2>&1
git commit -m "refactor(services): extract frame-formatting cluster to app/domain/frames.py (Phase 2, slice 3)" -m "Relocate the 7 pure frame-formatting helpers (to_fbdi_date, format_date_columns, blank_null_sentinels, resolve_today_tokens, dedup, mask_supplier_emails, safe_sheet_name) + their constants out of output_service into the domain (3299->3151 lines). fusion + operations routers now import from the domain, removing two more service->service couplings. Behaviour-preserving: differential byte-identical, 7 unit tests. NOTE: this bat stages all of backend/app + backend/tests so a re-run always commits a complete, bootable state." >> push_p2s3.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> push_p2s3.log 2>&1
echo --- HEAD after commit --- >> push_p2s3.log 2>&1
git log --oneline -2 >> push_p2s3.log 2>&1
echo --- confirm frames.py tracked at HEAD --- >> push_p2s3.log 2>&1
git ls-tree -r --name-only HEAD | findstr "domain/frames.py" >> push_p2s3.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_p2s3.log 2>&1
git push origin HEAD:main >> push_p2s3.log 2>&1
echo EXIT_PUSH=%errorlevel% >> push_p2s3.log 2>&1
echo ===== done ===== >> push_p2s3.log 2>&1
type push_p2s3.log
