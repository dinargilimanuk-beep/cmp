@echo off
setlocal DisableDelayedExpansion
set "CMP_PYTHON=%ProgramFiles%\Agisoft\Metashape Pro\python\python.exe"
if not exist "%~dp0cmp_qt.py" (
    echo Ekstrak seluruh isi ZIP ke satu folder sebelum membuka CMP.
    pause
    exit /b 1
)
if not exist "%CMP_PYTHON%" (
    echo Python bawaan Agisoft Metashape Pro tidak ditemukan.
    pause
    exit /b 1
)
"%CMP_PYTHON%" -B "%~dp0cmp_qt.py" "%~1"
if errorlevel 1 (
    echo CMP membutuhkan runtime Qt dan PySide2 bawaan Metashape Pro.
    pause
    exit /b 1
)
