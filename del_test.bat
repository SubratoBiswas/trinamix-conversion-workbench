@echo off
cd /d "%~dp0"
echo Before delete: > del_test.txt
dir ".git\objects\maintenance.lock" >> del_test.txt 2>&1
del /f /q ".git\objects\maintenance.lock" >> del_test.txt 2>&1
echo After delete: >> del_test.txt
dir ".git\objects\maintenance.lock" >> del_test.txt 2>&1
echo Errorlevel: %errorlevel% >> del_test.txt
type del_test.txt
pause
