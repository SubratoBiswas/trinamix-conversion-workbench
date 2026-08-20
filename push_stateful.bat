@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_stateful started %date% %time% ===== > push_stateful.log
git add backend/app/domain/rules/library/stateful_ops.py backend/app/domain/rules/registry.py backend/app/transformations/engine.py backend/tests/unit/test_rules.py >> push_stateful.log 2>&1
git commit -m "refactor(rules): migrate 8 stateful rule types to strategy classes" -m "Move CONCAT, COALESCE, CONDITIONAL, CASE_WHEN, BLANK_IF_EQUALS, PREFIX, SUFFIX, SUFFIX_WHEN out of engine._apply_one_rule into app/domain/rules/library/stateful_ops.py, reproduced verbatim on the existing domain-context helpers. Registered in registry.py; branches removed from engine.py (949->789 lines). Behaviour-preserving: byte-identical across a 70-case differential battery; 15/15 unit tests pass. 26/40 rule types now dispatch through the registry." >> push_stateful.log 2>&1
echo --- HEAD after commit --- >> push_stateful.log 2>&1
git log --oneline -1 >> push_stateful.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_stateful.log 2>&1
git push origin HEAD:main >> push_stateful.log 2>&1
echo EXITCODE=%errorlevel% >> push_stateful.log 2>&1
echo ===== done ===== >> push_stateful.log 2>&1
type push_stateful.log
