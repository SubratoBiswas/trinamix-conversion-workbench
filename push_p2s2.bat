@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_p2s2 started %date% %time% ===== > push_p2s2.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> push_p2s2.log 2>&1
echo --- staging Phase 2 slice 2 (5 files) --- >> push_p2s2.log 2>&1
git add backend/app/domain/rules/columns.py backend/app/services/output_service.py backend/app/services/strategy_overlay.py backend/app/services/learning_service.py backend/tests/unit/test_columns.py >> push_p2s2.log 2>&1
git status --porcelain >> push_p2s2.log 2>&1
git commit -m "refactor(services): extract rule column-reference analysis to app/domain/rules/columns.py (Phase 2, slice 2)" -m "Relocate the 4 pure functions (flat_cols/branch_columns/interpolated_columns/rule_referenced_columns) that enumerate which SOURCE columns a rule reads, out of output_service into the domain (3422->3299 lines). learning_service and strategy_overlay now import them from the domain, removing their coupling to output_service. Behaviour-preserving: 18-case differential byte-identical, 7 unit tests." >> push_p2s2.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> push_p2s2.log 2>&1
git log --oneline -2 >> push_p2s2.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_p2s2.log 2>&1
git push origin HEAD:main >> push_p2s2.log 2>&1
echo EXIT_PUSH=%errorlevel% >> push_p2s2.log 2>&1
echo ===== done ===== >> push_p2s2.log 2>&1
type push_p2s2.log
