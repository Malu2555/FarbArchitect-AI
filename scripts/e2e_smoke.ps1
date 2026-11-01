#Requires -Version 5.1
<#
.SYNOPSIS
    End-to-end smoke test for fabArchitect AI.

.DESCRIPTION
    Boots the backend on :8080 and the Vite dev server on :5173 (both as
    background processes), verifies health + simulate through the Vite proxy,
    then tears both processes down and exits non-zero on any failure.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/e2e_smoke.ps1
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8080,
    [int]$FrontendPort = 5173,
    [int]$Seed = 42,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "[e2e] FAIL: backend virtualenv not found at $python" -ForegroundColor Red
    exit 1
}

function Wait-ForHttp {
    param([string]$Url, [int]$TimeoutSeconds = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        } catch {
            Start-Sleep -Milliseconds 750
        }
    }
    throw "Timed out waiting for $Url"
}

Write-Host "[e2e] booting backend on port $BackendPort" -ForegroundColor Cyan
$backend = Start-Process -FilePath $python `
    -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port',"$BackendPort" `
    -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden

Write-Host "[e2e] booting vite dev server on port $FrontendPort" -ForegroundColor Cyan
$frontend = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c','npm run dev' `
    -WorkingDirectory $frontendDir -PassThru -WindowStyle Hidden

try {
    $null = Wait-ForHttp -Url "http://127.0.0.1:$FrontendPort/" -TimeoutSeconds $TimeoutSeconds
    Write-Host '[e2e] dev server up' -ForegroundColor Green

    $health = Wait-ForHttp -Url "http://127.0.0.1:$FrontendPort/api/v1/health" -TimeoutSeconds $TimeoutSeconds
    Write-Host "[e2e] health $($health.StatusCode) - proxy OK" -ForegroundColor Green

    $payload = '{"seed":' + $Seed + '}'
    $sim = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/api/v1/simulate" `
        -Method POST -Body $payload -ContentType 'application/json' `
        -UseBasicParsing -TimeoutSec 240

    if ($sim.StatusCode -ne 200) { throw "simulate returned $($sim.StatusCode)" }

    $body = $sim.Content | ConvertFrom-Json
    if ($null -eq $body.before) { throw 'response missing before' }
    if ($null -eq $body.after) { throw 'response missing after' }
    if ($null -eq $body.scene) { throw 'response missing scene' }
    if ($body.corrections.Count -lt 1) { throw 'response has no correction turns' }

    # Stage 2 spatial renderer must always be present, even on a clean layout.
    if ($null -eq $body.spatial) { throw 'response missing spatial payload' }
    $spec = $body.spatial.spatial_spec
    if ($null -eq $spec) { throw 'spatial payload missing spatial_spec' }
    if ($null -eq $spec.footprints -or $spec.footprints.Count -lt 1) {
        throw 'spatial spec has no footprints'
    }
    if ($spec.canvas.no_overlap -ne $true) { throw 'spatial spec reports overlaps' }
    if ($spec.canvas.no_clipping -ne $true) { throw 'spatial spec reports clipping' }
    if ($spec.canvas.canvas_px[0] -ne 500 -or $spec.canvas.canvas_px[1] -ne 500) {
        throw "unexpected canvas size: $($spec.canvas.canvas_px)"
    }
    if ($body.spatial.preview_png_data_url -notmatch '^data:image/png;base64,') {
        throw 'preview_png_data_url is not a base64 PNG data URL'
    }

    $beforePeak = [math]::Round([double]$body.before.peak_c, 2)
    $afterPeak = [math]::Round([double]$body.after.peak_c, 2)
    $turns = $body.corrections.Count

    Write-Host "[e2e] simulate OK - seed=$($body.seed) before=${beforePeak}C after=${afterPeak}C turns=$turns" -ForegroundColor Green
    Write-Host '[e2e] PASS' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "[e2e] FAIL: $_" -ForegroundColor Red
    exit 1
} finally {
    try { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue } catch {}
    try { Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue } catch {}
    Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
