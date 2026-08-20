@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_dateops started %date% %time% ===== > push_dateops.log
git add backend/app/domain/dates/fbdi_date.py backend/app/domain/rules/library/date_ops.py backend/app/domain/rules/registry.py backend/app/transformations/engine.py backend/tests/unit/test_rules.py >> push_dateops.log 2>&1
git commit -m "refactor(rules): migrate date-ops rule types to strategies + relocate date helpers" -m "Migrate FORMAT_DATE, DATE_FORMAT, CONDITIONAL_DATE, COMPUTED out of engine._apply_one_rule into app/domain/rules/library/date_ops.py. Relocate the last date helpers (OUT_DATE_FORMAT, oracle_date_to_py, parse_any_date + Oracle token table) into app/domain/dates/fbdi_date.py; engine.py no longer owns any date-format knowledge (789->642 lines, dead datetime/uuid imports dropped). Behaviour-preserving: 47-case date battery and 70-case stateful regression both byte-identical; 20/20 unit tests pass. 30/40 rule types now dispatch through the registry." >> push_dateops.log 2>&1
echo --- HEAD after commit --- >> push_dateops.log 2>&1
git log --oneline -1 >> push_dateops.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_dateops.log 2>&1
git push origin HEAD:main >> push_dateops.log 2>&1
echo EXITCODE=%errorlevel% >> push_dateops.log 2>&1
echo ===== done ===== >> push_dateops.log 2>&1
type push_dateops.log
