# Phase 4: build the Project Genesis desktop app.
#   1. C++ engine            -> engine/build/engine.exe
#   2. Python orchestrator   -> orchestrator/dist/orchestrator.exe (PyInstaller, one file)
#   3. Copy both into src-tauri/binaries with the target-triple suffix Tauri expects
#   4. tauri build           -> src-tauri/target/release/bundle/nsis/*-setup.exe
#
# Prereqs: .venv with orchestrator/requirements.txt + pyinstaller, Rust (rustup),
# VS C++ Build Tools, Node (npm install at repo root for the Tauri CLI).
param([switch]$SkipOrchestrator)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root '.venv\Scripts\python.exe'

& (Join-Path $root 'engine\build.ps1')

if (-not $SkipOrchestrator) {
    Push-Location (Join-Path $root 'orchestrator')
    try {
        $ErrorActionPreference = 'Continue'
        & $py -m PyInstaller --noconfirm --onefile --console --name orchestrator `
            --workpath build\pyi --distpath dist --specpath build `
            --collect-submodules uvicorn --collect-submodules genesis `
            --collect-all chromadb --collect-all chromadb_rust_bindings --collect-all onnxruntime --collect-all tokenizers `
            --copy-metadata chromadb --copy-metadata opentelemetry-sdk --copy-metadata opentelemetry-api `
            orchestrator_main.py
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    } finally { Pop-Location }
}

$triple = (& rustc -vV | Select-String '^host:').ToString().Split(' ')[1]
$bin = Join-Path $root 'src-tauri\binaries'
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $root 'engine\build\engine.exe') (Join-Path $bin "engine-$triple.exe") -Force
Copy-Item (Join-Path $root 'orchestrator\dist\orchestrator.exe') (Join-Path $bin "orchestrator-$triple.exe") -Force

Push-Location $root
try {
    $ErrorActionPreference = 'Continue'  # native tools log to stderr; rely on exit codes
    npx tauri build
    if ($LASTEXITCODE -ne 0) { throw 'tauri build failed' }
} finally { Pop-Location }
Get-ChildItem (Join-Path $root 'src-tauri\target\release\bundle\nsis\*.exe') | Select-Object FullName, Length
