@echo off
cd /d "%~dp0"
where py >nul 2>&1 && (py -3 app.py & goto :end)
where python >nul 2>&1 && (python app.py & goto :end)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
:end
pause
