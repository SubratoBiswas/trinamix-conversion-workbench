@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== fix_dateops started %date% %time% ===== > fix_dateops.log
echo --- staging the complete date-ops slice (adds the 2 missing files) --- >> fix_dateops.log 2>&1
git add backend/app/domain/rules/library/date_ops.py backend/app/domain/dates/fbdi_date.py backend/app/domain/rules/registry.py backend/app/transformations/engine.py backend/tests/unit/test_rules.py >> fix_dateops.log 2>&1
echo --- staged status --- >> fix_dateops.log 2>&1
git status --porcelain >> fix_dateops.log 2>&1
git commit -m "fix(rules): add missing date_ops module + relocated date helpers (completes date-ops slice)" -m "Commit 25a7448 shipped the date-ops edits to registry.py/engine.py/test_rules.py but NOT the new app/domain/rules/library/date_ops.py module or the updated app/domain/dates/fbdi_date.py helpers it imports, so the container crashed at import (ModuleNotFoundError: app.domain.rules.library.date_ops) and Render deploys timed out. This adds the two missing files. Behaviour-preserving: 47-case date battery + 70-case stateful regression byte-identical; 20/20 unit tests pass. 30/40 rule types now on the registry." >> fix_dateops.log 2>&1
echo --- HEAD after commit --- >> fix_dateops.log 2>&1
git log --oneline -4 >> fix_dateops.log 2>&1
echo --- confirm date_ops.py now tracked at HEAD --- >> fix_dateops.log 2>&1
git ls-tree -r --name-only HEAD | findstr date_ops >> fix_dateops.log 2>&1
echo ===== pushing HEAD to origin main ===== >> fix_dateops.log 2>&1
git push origin HEAD:main >> fix_dateops.log 2>&1
echo EXITCODE=%errorlevel% >> fix_dateops.log 2>&1
echo ===== done ===== >> fix_dateops.log 2>&1
type fix_dateops.log
