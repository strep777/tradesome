@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

rem Prefer pythonw so double-clicking opens no console window.
where pythonw.exe >nul 2>nul
if errorlevel 1 goto trypy
pythonw.exe -c "import tradesome" >nul 2>nul
if errorlevel 1 goto console
start "" pythonw.exe -m tradesome %*
exit /b 0

:trypy
where py.exe >nul 2>nul
if errorlevel 1 goto console
py -3w -c "import tradesome" >nul 2>nul
if errorlevel 1 goto console
start "" py -3w -m tradesome %*
exit /b 0

:console
where python.exe >nul 2>nul
if errorlevel 1 goto console_py
echo Starting TradeSome with a console window...
python.exe -m tradesome %*
if errorlevel 1 pause
exit /b %errorlevel%

:console_py
where py.exe >nul 2>nul
if errorlevel 1 goto nopython
echo Starting TradeSome with a console window...
py -3 -m tradesome %*
if errorlevel 1 pause
exit /b %errorlevel%

:nopython
echo Python 3.9+ was not found on PATH.
echo Install Python 3.9 or later (tick "Add Python to PATH") and run this file again.
pause
exit /b 1
