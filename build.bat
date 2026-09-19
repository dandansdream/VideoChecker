@echo off
REM VideoChecker build script - requires Python 3.10+ and PyInstaller
REM Install:  pip install pyinstaller
REM Build:    build.bat
python -m PyInstaller --onefile --noconsole --name VideoChecker --clean -y video_checker_gui.py
echo.
echo Done. EXE is at dist\VideoChecker.exe
pause
