@echo off
setlocal
cd /d "%~dp0"

rem --- find Python (launcher "py" or "python") ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.9+: https://www.python.org/downloads/
  goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" || (
  echo [ERROR] Python 3.9 or newer is required.
  goto :fail
)

rem --- create the venv if missing ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv || goto :fail
) else (
  echo .venv already exists - updating dependencies.
)

".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Done. Next:
echo   1^) run_extract.bat   - extract data from the game (once)
echo   2^) run_hints.bat     - generate the hint report
pause
exit /b 0

:fail
echo.
echo Setup failed.
pause
exit /b 1
