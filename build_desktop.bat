@echo off
rem Baut die eigenstaendige Windows-.exe von Klarcash per Doppelklick.
rem Legt bei Bedarf eine .venv an, installiert requirements-desktop.txt
rem und ruft anschliessend PyInstaller mit klarcash.spec auf.
setlocal
cd /d "%~dp0"

set "PY=python"
if exist "venv\Scripts\python.exe"  set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not exist ".venv\Scripts\python.exe" if not exist "venv\Scripts\python.exe" (
    echo [build] Lege virtuelle Umgebung .venv an ...
    python -m venv .venv || goto :error
    set "PY=.venv\Scripts\python.exe"
)

echo [build] Installiere Desktop-Abhaengigkeiten ...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements-desktop.txt || goto :error

echo [build] Baue Klarcash.exe mit PyInstaller ...
"%PY%" -m PyInstaller klarcash.spec --workpath desktop_build/build --distpath desktop_build/dist || goto :error

echo.
echo [build] Fertig: desktop_build\dist\Klarcash.exe
goto :eof

:error
echo.
echo [build] FEHLER beim Build - siehe Ausgabe oben.
exit /b 1
