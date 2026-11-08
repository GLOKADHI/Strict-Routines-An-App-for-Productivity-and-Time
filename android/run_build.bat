@echo off
cd /d "%~dp0"
set LOGFILE=build_output.log
echo Starting assembleDebug at %TIME% > "%LOGFILE%"
call .\gradlew.bat assembleDebug < nul >> "%LOGFILE%" 2>&1
echo FINISHED_EXIT_CODE=%ERRORLEVEL% at %TIME% >> "%LOGFILE%"
