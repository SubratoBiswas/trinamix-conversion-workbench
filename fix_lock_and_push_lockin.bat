@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== fix_lock_and_push_lockin started %date% %time% ===== > fix_lock_and_push_lockin.log
echo --- clearing any stale git lock files --- >> fix_lock_and_push_lockin.log 2>&1
if exist .git\index.lock del /f /q .git\index.lock && echo removed index.lock >> fix_lock_and_push_lockin.log 2>&1
if exist .git\HEAD.lock del /f /q .git\HEAD.lock && echo removed HEAD.lock >> fix_lock_and_push_lockin.log 2>&1
if exist .git\refs\heads\main.lock del /f /q .git\refs\heads\main.lock && echo removed main.lock >> fix_lock_and_push_lockin.log 2>&1
echo --- staging the 4 lock-in files --- >> fix_lock_and_push_lockin.log 2>&1
git add backend/app/domain/README.md backend/tests/test_format_date_rule.py backend/tests/test_boolean_conditions.py docs/Trinamix_Architecture_AsBuilt_Phase1.md >> fix_lock_and_push_lockin.log 2>&1
echo EXIT_ADD=%errorlevel% >> fix_lock_and_push_lockin.log 2>&1
git commit -m "docs+test: lock in Phase 1 (rule-engine migration complete)" -m "Add app/domain/README.md (domain layout map) and docs/Trinamix_Architecture_AsBuilt_Phase1.md. Point two tests that reached into relocated engine internals at their new domain homes: test_format_date_rule (_oracle_date_to_py -> domain.dates.fbdi_date.oracle_date_to_py) and test_boolean_conditions (_COMPARISON_OPS -> domain.rules.context). No runtime behaviour change." >> fix_lock_and_push_lockin.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> fix_lock_and_push_lockin.log 2>&1
echo --- HEAD after commit (should be a NEW hash, not e542830) --- >> fix_lock_and_push_lockin.log 2>&1
git log --oneline -2 >> fix_lock_and_push_lockin.log 2>&1
echo ===== pushing HEAD to origin main ===== >> fix_lock_and_push_lockin.log 2>&1
git push origin HEAD:main >> fix_lock_and_push_lockin.log 2>&1
echo EXIT_PUSH=%errorlevel% >> fix_lock_and_push_lockin.log 2>&1
echo ===== done ===== >> fix_lock_and_push_lockin.log 2>&1
type fix_lock_and_push_lockin.log
