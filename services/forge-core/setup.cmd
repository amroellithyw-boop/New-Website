@echo off
REM ForgeOS one-time installer for Windows. Double-click this file.
REM It creates a private Python environment, installs ForgeOS, and asks for your keys.
cd /d "%~dp0"
where py >nul 2>nul || where python >nul 2>nul || (
  echo Python is not installed. Install it from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" on the first screen, then double-click this file again.
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating the private environment...
  (py -3 -m venv .venv 2>nul) || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"
echo.
echo Checking the install...
forge bench
echo.
forge setup
echo.
echo All set. From now on, open this folder, double-click forge.cmd, and type commands.
pause
