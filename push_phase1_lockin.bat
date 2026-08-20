@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_phase1_lockin started %date% %time% ===== > push_phase1_lockin.log
echo --- staging: in-repo domain map, 2 test fixes, as-built doc --- >> push_phase1_lockin.log 2>&1
git add backend/app/domain/README.md backend/tests/test_format_date_rule.py backend/tests/test_boolean_conditions.py docs/Trinamix_Architecture_AsBuilt_Phase1.md >> push_phase1_lockin.log 2>&1
echo --- staged files --- >> push_phase1_lockin.log 2>&1
git status --porcelain >> push_phase1_lockin.log 2>&1
git commit -m "docs+test: lock in Phase 1 (rule-engine migration complete)" -m "Add app/domain/README.md (domain layout map) and docs/Trinamix_Architecture_AsBuilt_Phase1.md. Point two tests that reached into relocated engine internals at their new domain homes: test_format_date_rule (_oracle_date_to_py -> domain.dates.fbdi_date.oracle_date_to_py) and test_boolean_conditions (_COMPARISON_OPS -> domain.rules.context). No runtime behaviour change. Domain unit suite: 59 passed." >> push_phase1_lockin.log 2>&1
echo --- HEAD after commit --- >> push_phase1_lockin.log 2>&1
git log --oneline -2 >> push_phase1_lockin.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_phase1_lockin.log 2>&1
git push origin HEAD:main >> push_phase1_lockin.log 2>&1
echo EXITCODE=%errorlevel% >> push_phase1_lockin.log 2>&1
echo ===== done ===== >> push_phase1_lockin.log 2>&1
type push_phase1_lockin.log
