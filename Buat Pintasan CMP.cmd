@echo off
setlocal DisableDelayedExpansion
set "CMP_SHORTCUT_DIR=%~dp0"
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $dir=$env:CMP_SHORTCUT_DIR; $target=Join-Path $dir 'CMP.cmd'; $icon=Join-Path $dir 'CMP.ico'; if (!(Test-Path -LiteralPath $target -PathType Leaf) -or !(Test-Path -LiteralPath $icon -PathType Leaf)) { throw 'Ekstrak seluruh isi ZIP ke satu folder.' }; $shell=New-Object -ComObject WScript.Shell; $shortcut=$shell.CreateShortcut((Join-Path $dir 'CMP.lnk')); $shortcut.TargetPath=$target; $shortcut.WorkingDirectory=$dir; $shortcut.IconLocation=$icon + ',0'; $shortcut.Description='Coral Marine Photogrammetry'; $shortcut.Save()"
if errorlevel 1 (
    echo Pintasan CMP gagal dibuat.
    pause
    exit /b 1
)
echo Pintasan CMP dibuat di folder ini. Anda dapat menyalinnya ke Desktop.
pause
