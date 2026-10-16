@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
"%PYTHON_EXE%" -m ensurepip --upgrade
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" -m PyInstaller --noconfirm --onefile --console --name OOZWorker --distpath assets\ooz --workpath build\ooz --specpath assets\ooz assets\ooz\worker.py
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean MHWLayerStudio.spec
if errorlevel 1 exit /b 1
if not defined ISCC set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%~dp0tools\InnoSetup\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" installer.iss
  if errorlevel 1 exit /b 1
) else (
  echo Build complete: dist\MHWLayerStudio\MHWLayerStudio.exe
  echo Install Inno Setup 6 and run: ISCC installer.iss
)
