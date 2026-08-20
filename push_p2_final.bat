@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_p2_final started %date% %time% ===== > push_p2_final.log
if exist .git\index.lock del /f /q .git\index.lock && echo cleared stale index.lock >> push_p2_final.log 2>&1
echo --- staging backend/app + backend/tests + docs (partial-proof) --- >> push_p2_final.log 2>&1
git add backend/app backend/tests docs >> push_p2_final.log 2>&1
echo --- staged files --- >> push_p2_final.log 2>&1
git status --porcelain >> push_p2_final.log 2>&1
git commit -m "refactor(services): complete Phase 2 — extract last frame/column helpers; services now thin over the domain" -m "Slice 5: move is_attribute_column (+_ATTR_RE), normalize_columns, header_label out of output_service into app/domain/frames.py (3071->3042 lines); routers/fusion imports normalize_columns from the domain. Update app/domain/README.md and add docs/Trinamix_Architecture_Phase2.md. Phase 2 complete: output_service 3657->~3042, five service->service couplings removed, all pure logic (indexes, columns, row, frames) in the domain; async orchestrators left as thin orchestration by design. Behaviour-preserving: differential byte-identical, 10 frames unit tests." >> push_p2_final.log 2>&1
echo EXIT_COMMIT=%errorlevel% >> push_p2_final.log 2>&1
echo --- HEAD after commit --- >> push_p2_final.log 2>&1
git log --oneline -2 >> push_p2_final.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_p2_final.log 2>&1
git push origin HEAD:main >> push_p2_final.log 2>&1
echo EXIT_PUSH=%errorlevel% >> push_p2_final.log 2>&1
echo ===== done ===== >> push_p2_final.log 2>&1
type push_p2_final.log
