# Builds engine/build/engine.exe.
# Uses g++ or clang++ if one is on PATH, otherwise the zig C++ toolchain from
# the project venv (pip install ziglang), which needs no system install.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here 'src\main.cpp'
$out = Join-Path $here 'build\engine.exe'
New-Item -ItemType Directory -Force (Join-Path $here 'build') | Out-Null

$cxx = @('g++', 'clang++') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if ($cxx) {
    & $cxx -std=c++17 -O2 -Wall -Wextra -static -o $out $src
} else {
    $py = Join-Path $here '..\.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "No C++ compiler found and no .venv; run: python -m venv .venv; .venv\Scripts\pip install ziglang" }
    & $py -m ziglang c++ -std=c++17 -O2 -Wall -Wextra -o $out $src
}
if ($LASTEXITCODE -ne 0) { throw "engine build failed" }
Write-Host "Built $out"
