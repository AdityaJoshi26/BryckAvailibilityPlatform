<#
  Seeds all machines from machines.json into the Bryck Inventory API.
  Pure PowerShell — no Python required.

  Usage:
    .\seed_machines.ps1
    .\seed_machines.ps1 -ApiBase "http://192.168.0.165:8000"
#>
param(
    [string]$ApiBase  = "http://192.168.0.165:8000",
    [string]$JsonPath = "$PSScriptRoot\machines.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $JsonPath)) {
    Write-Host "machines.json not found at $JsonPath" -ForegroundColor Red
    exit 1
}

$endpoint = "$($ApiBase.TrimEnd('/'))/inventory"
$machines = (Get-Content $JsonPath -Raw | ConvertFrom-Json).machines
Write-Host "Seeding $($machines.Count) machines -> $endpoint`n" -ForegroundColor Cyan

# Avoid Windows PowerShell keep-alive socket reuse (the "underlying connection
# was closed: an unexpected error occurred on a receive" failures).
[System.Net.ServicePointManager]::MaxServicePointIdleTime = 0

$MaxRetries  = 3
$added = 0; $skipped = 0; $failed = 0

foreach ($m in $machines) {
    $body = $m | ConvertTo-Json -Depth 10
    $ip   = $m.ip_address

    $attempt   = 0
    $done      = $false
    $lastError = $null

    while (-not $done -and $attempt -lt $MaxRetries) {
        $attempt++
        try {
            # -DisableKeepAlive forces a fresh TCP connection per request so we
            # never reuse a socket uvicorn has already closed.
            Invoke-RestMethod -Uri $endpoint -Method Post `
                -ContentType "application/json" -Body $body -DisableKeepAlive | Out-Null
            Write-Host "  added    $ip" -ForegroundColor Green
            $added++
            $done = $true
        }
        catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }

            if ($status -eq 409) {
                Write-Host "  exists   $ip (skipped)" -ForegroundColor Yellow
                $skipped++
                $done = $true
            }
            elseif ($status) {
                # Real HTTP error (4xx/5xx) — don't retry, report it.
                Write-Host "  FAILED   $ip -> HTTP $status $($_.Exception.Message)" -ForegroundColor Red
                $failed++
                $done = $true
            }
            else {
                # Transient transport error (dropped/closed socket) — retry.
                $lastError = $_.Exception.Message
                if ($attempt -lt $MaxRetries) {
                    Write-Host "  retry    $ip (attempt $attempt/$MaxRetries)" -ForegroundColor DarkYellow
                    Start-Sleep -Milliseconds 300
                }
            }
        }
    }

    if (-not $done) {
        Write-Host "  FAILED   $ip -> $lastError" -ForegroundColor Red
        $failed++
    }
}

Write-Host "`nDone. added=$added skipped=$skipped failed=$failed" -ForegroundColor Cyan
