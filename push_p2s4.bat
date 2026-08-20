@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_p2s4 started %date% %time% ===== > push_p2s4.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> push_p2s4.log 2>&1
echo --- staging ALL of backend/app + backend/tests (partial-proof) --- >> push_p2s4.log 2>&1
git add backend/app backend/tests >> push_p2s4.log 2>&1
echo --- staged files --- >> push_p2s4.log 2>&1
git status --porcelain >> push_p2s4.log 2>&1
git commit -m "refactor(services): extract per-row context to app/domain/rules/row.py (Phase 2, slice 4)" -m "Relocate the _RowWithTargets class + its _MISSING sentinel out of output_service into the domain, next to the row helpers in rules/context.py. It is the dict-like row a rule reads (get/iter/keys/contains), exposing source columns plus any target column already computed earlier in the sequence. output_service imports it back as _RowWithTargets (3151->3071 lines). No external importers. Behaviour-preserving: differential byte-identical across get/getitem/contains/iter/keys/len, 6 unit tests." >> push_p2s4.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> push_p2s4.log 2>&1
echo --- HEAD after commit --- >> push_p2s4.log 2>&1
git log --oneline -2 >> push_p2s4.log 2>&1
echo --- confirm row.py tracked at HEAD --- >> push_p2s4.log 2>&1
git ls-tree -r --name-only HEAD | findstr "rules/row.py" >> push_p2s4.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_p2s4.log 2>&1
git push origin HEAD:main >> push_p2s4.log 2>&1
echo EXIT_PUSH=%errorlevel% >> push_p2s4.log 2>&1
echo ===== done ===== >> push_p2s4.log 2>&1
type push_p2s4.log
