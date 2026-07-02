@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== deploy_push started %date% %time% ===== > deploy_push.log
git rev-parse --abbrev-ref HEAD >> deploy_push.log 2>&1
git log --oneline -1 >> deploy_push.log 2>&1
echo --- commits ahead of origin/main --- >> deploy_push.log 2>&1
git log --oneline origin/main..HEAD >> deploy_push.log 2>&1
echo ===== pushing HEAD to origin main ===== >> deploy_push.log 2>&1
git push origin HEAD:main >> deploy_push.log 2>&1
echo EXITCODE=%errorlevel% >> deploy_push.log 2>&1
echo ===== done ===== >> deploy_push.log 2>&1
