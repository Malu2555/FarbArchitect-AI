$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "Backend virtualenv not found. See README for bootstrap steps." -ForegroundColor Red
    exit 1
}

Set-Location (Join-Path $root 'backend')
$env:UVICORN_RELOAD = '1'
& $python 'run_local.py'
