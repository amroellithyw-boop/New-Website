@echo off
REM Opens a ForgeOS terminal in this folder. Type `forge` commands here.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
echo ForgeOS ready. Try:  forge providers      forge qbo connect --tenant sandbox      forge run
cmd /k
