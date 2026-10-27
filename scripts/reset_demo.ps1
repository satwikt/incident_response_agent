<#
  Reset the demo to a clean take.

  Removes ONLY this project's two data volumes, by exact name:
    <project>_copilot-data     incidents, chats, events
    <project>_hindsight-data   Hindsight memory (skip with -KeepMemory)
  Loki, Tempo and Prometheus are recreated too (they keep telemetry only, in the container, no named volume), so logs and
  traces from earlier takes cannot leak into the next diagnosis; skip that with -KeepTelemetry.
  The shop database, images, .env and every other Docker volume are left alone. MEMORY_MODE is not touched.

  Usage:  .\scripts\reset_demo.ps1             # fresh take: first fault = ultra tier, repeat = nano
          .\scripts\reset_demo.ps1 -KeepMemory # keep what the copilot has learned, clear incidents and chats
#>
param([switch]$KeepMemory, [switch]$KeepTelemetry, [string]$ChaosKey = $env:CHAOS_KEY)   # the fault reset needs the key when CHAOS_KEY is set

# Docker writes progress to stderr, which Windows PowerShell 5.1 would turn into errors under "Stop": check exit codes instead.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Run([string]$what, [scriptblock]$cmd) {
    & $cmd 2>&1 | ForEach-Object { "$_" } | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

$project = (docker compose config --format json 2>$null | ConvertFrom-Json).name
if (-not $project) { throw "Could not determine the compose project name (is Docker running?)." }

$hdr = @{}; if ($ChaosKey) { $hdr["X-Chaos-Key"] = $ChaosKey }
try { Invoke-RestMethod -Method Post -Uri "http://localhost:9922/api/faults/reset" -Headers $hdr | Out-Null } catch { Write-Host "(faults not reset: frontend unreachable, or it needs the chaos key: pass -ChaosKey or set `$env:CHAOS_KEY)" }

$services = @("copilot", "adapter", "hindsight")
if (-not $KeepTelemetry) { $services += @("loki", "tempo", "prometheus") }
Run "stop"   { docker compose stop @services }
Run "remove" { docker compose rm -f @services }

$volumes = @("${project}_copilot-data")
if (-not $KeepMemory) { $volumes += "${project}_hindsight-data" }
foreach ($v in $volumes) {
    $exists = docker volume ls --format "{{.Name}}" | Where-Object { $_ -eq $v }
    if ($exists) { Run "remove volume $v" { docker volume rm $v }; Write-Host "removed volume $v" }
    else { Write-Host "volume $v not found (already clean)" }
}

Run "start" { docker compose up -d @services }
Write-Host "`nDone. Copilot: http://localhost:9911/dashboard   (Hindsight needs ~30 s to 5 min to be ready on a fresh volume)"
