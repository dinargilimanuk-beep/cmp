@echo off
setlocal DisableDelayedExpansion
set "python=%ProgramFiles%\Agisoft\Metashape Pro\python\python.exe"

if not exist "%~dp0launcher_ortho.py" (
    echo Ekstrak seluruh isi ZIP ke satu folder sebelum membuka launcher.
    pause
    exit /b 1
)

if exist "%python%" (
    "%python%" -B "%~dp0launcher_ortho.py" "%~1"
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        echo Pasang Python 3 dengan Tkinter atau gunakan Python bawaan Metashape Pro.
        pause
        exit /b 1
    )
    py -3 -B "%~dp0launcher_ortho.py" "%~1"
)
if not errorlevel 1 exit /b 0
echo Launcher tidak bisa dibuka. Periksa pesan di atas.
pause
exit /b 1
