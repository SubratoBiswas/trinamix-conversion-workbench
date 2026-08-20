@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_p2s1 started %date% %time% ===== > push_p2s1.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> push_p2s1.log 2>&1
echo --- staging Phase 2 slice 1 (3 files) --- >> push_p2s1.log 2>&1
git add backend/app/domain/rules/indexes.py backend/app/services/output_service.py backend/tests/unit/test_indexes.py >> push_p2s1.log 2>&1
git status --porcelain >> push_p2s1.log 2>&1
git commit -m "refactor(services): extract ctx-index builders to app/domain/rules/indexes.py (Phase 2, slice 1)" -m "Relocate the 5 pure index builders (self/sequence/group_first/city_country/city_case) out of output_service into the domain, next to the lookup strategies that consume the indexes they build. output_service re-imports them under their old names (3657->3422 lines). Behaviour-preserving: 16-case differential byte-identical, 6 unit tests. The config-gatherers that feed these stay in the service layer." >> push_p2s1.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> push_p2s1.log 2>&1
echo --- HEAD after commit --- >> push_p2s1.log 2>&1
git log --oneline -2 >> push_p2s1.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_p2s1.log 2>&1
git push origin HEAD:main >> push_p2s1.log 2>&1
echo EXIT_PUSH=%errorlevel% >> push_p2s1.log 2>&1
echo ===== done ===== >> push_p2s1.log 2>&1
type push_p2s1.log
