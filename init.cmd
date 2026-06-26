@echo off
setlocal
cd /d "%~dp0"
python scripts\harness\init_check.py %*
exit /b %ERRORLEVEL%
