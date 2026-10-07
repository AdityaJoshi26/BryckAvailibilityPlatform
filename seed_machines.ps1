<#
  Seeds all machines from machines.json into the Bryck Inventory API.
  Pure PowerShell — no Python required.

  Usage:
    .\seed_machines.ps1
    .\seed_machines.ps1 -ApiBase "http://192.168.0.165:8000"
#>
param(
    [string]$ApiBase  = "http://182.168.0.165:8000",
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

$added = 0; $skipped = 0; $failed = 0

foreach ($m in $machines) {
    $body = $m | ConvertTo-Json -Depth 10
    $ip   = $m.ip_address
    try {
        Invoke-RestMethod -Uri $endpoint -Method Post `
            -ContentType "application/json" -Body $body | Out-Null
        Write-Host "  added    $ip" -ForegroundColor Green
        $added++
    }
    catch {
        $status = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 409) {
            Write-Host "  exists   $ip (skipped)" -ForegroundColor Yellow
            $skipped++
        }
        else {
            Write-Host "  FAILED   $ip -> $($_.Exception.Message)" -ForegroundColor Red
            $failed++
        }
    }
}

Write-Host "`nDone. added=$added skipped=$skipped failed=$failed" -ForegroundColor Cyan
