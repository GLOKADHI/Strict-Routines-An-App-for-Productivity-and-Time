@echo off
cd /d "%~dp0"
set LOGFILE=test_output.log
echo Starting testDebugUnitTest at %TIME% > "%LOGFILE%"
call .\gradlew.bat testDebugUnitTest >> "%LOGFILE%" 2>&1
echo FINISHED_EXIT_CODE=%ERRORLEVEL% at %TIME% >> "%LOGFILE%"
