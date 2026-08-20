@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== fix_p2s2 started %date% %time% ===== > fix_p2s2.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> fix_p2s2.log 2>&1
echo --- staging the COMPLETE slice 2 (adds the files 107177b was missing) --- >> fix_p2s2.log 2>&1
git add backend/app/domain/rules/columns.py backend/app/services/output_service.py backend/app/services/strategy_overlay.py backend/app/services/learning_service.py backend/tests/unit/test_columns.py >> fix_p2s2.log 2>&1
echo --- staged files --- >> fix_p2s2.log 2>&1
git status --porcelain >> fix_p2s2.log 2>&1
git commit -m "fix(services): complete Phase 2 slice 2 — add missing columns.py + importer changes" -m "Commit 107177b shipped slice 2's output_service.py (which imports app.domain.rules.columns) but NOT columns.py or the learning_service/strategy_overlay importer changes, so the container crashed at import (ModuleNotFoundError) and Render timed out. This adds the missing files. Behaviour-preserving: 18-case differential byte-identical, 7 unit tests." >> fix_p2s2.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> fix_p2s2.log 2>&1
echo --- HEAD after commit --- >> fix_p2s2.log 2>&1
git log --oneline -3 >> fix_p2s2.log 2>&1
echo --- confirm columns.py now tracked at HEAD --- >> fix_p2s2.log 2>&1
git ls-tree -r --name-only HEAD | findstr "rules/columns.py" >> fix_p2s2.log 2>&1
echo ===== pushing HEAD to origin main ===== >> fix_p2s2.log 2>&1
git push origin HEAD:main >> fix_p2s2.log 2>&1
echo EXIT_PUSH=%errorlevel% >> fix_p2s2.log 2>&1
echo ===== done ===== >> fix_p2s2.log 2>&1
type fix_p2s2.log
