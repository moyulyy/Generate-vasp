@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw incar_gui.py %* & exit /b 0)
python incar_gui.py %*
if errorlevel 1 pause
