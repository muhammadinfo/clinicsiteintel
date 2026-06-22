# Builds the ClinicSiteIntel.exe (one-folder PyInstaller build) from app/main.py.
# Run from the ClinicSiteIntel directory: powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

pyinstaller --noconfirm --windowed --name ClinicSiteIntel `
    --paths "$root\app" `
    --add-data "$root\app;app" `
    "$root\app\main.py"

Write-Host ""
Write-Host "Build complete. Executable folder: $root\dist\ClinicSiteIntel\"
Write-Host "Next: run installer\build_installer.ps1 (requires Inno Setup) to produce a single .exe installer."
