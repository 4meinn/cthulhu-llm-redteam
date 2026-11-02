# Build the single-file Windows desktop app: dist\cthulhu.exe
# Run from the repository root with the project's virtualenv active:
#   pip install -e ".[web,desktop]"
#   .\tools\build_exe.ps1
$ErrorActionPreference = "Stop"

python -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name cthulhu `
    --collect-submodules uvicorn `
    --add-data "cthulhu\web\static;cthulhu\web\static" `
    --add-data "cthulhu\corpus\data;cthulhu\corpus\data" `
    tools\desktop_entry.py

Write-Host "built dist\cthulhu.exe"
