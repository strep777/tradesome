@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
where python.exe >nul 2>nul
if errorlevel 1 goto use_py
python.exe -m tradesome --log-level DEBUG %*
if errorlevel 1 pause
exit /b %errorlevel%

:use_py
where py.exe >nul 2>nul
if errorlevel 1 goto nopython
py -3 -m tradesome --log-level DEBUG %*
if errorlevel 1 pause
exit /b %errorlevel%

:nopython
echo Python 3.9+ was not found on PATH.
echo Install Python 3.9 or later (tick "Add Python to PATH") and run this file again.
pause
exit /b 1
