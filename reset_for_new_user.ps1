#Requires -Version 5.1
<#
.SYNOPSIS
    Reset data before first run for a new user.
.DESCRIPTION
    Deletes logs, trade database and state snapshots from previous owner.
    Run ONCE before first use.
#>

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogsDir = Join-Path $RootDir "logs"
$ConfigDir = Join-Path $RootDir "config"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " XAUUSD Scalper - Reset for New User" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "This script will delete:" -ForegroundColor Yellow
Write-Host "  - logs/events.jsonl (event log)"
Write-Host "  - logs/trades.db (trade database)"
Write-Host "  - logs/state_snapshot.json (state snapshot)"
Write-Host "  - logs/*.log (other logs)"
Write-Host ""
Write-Host "Config (config/default.yaml) will be kept," -ForegroundColor Green
Write-Host "but you need to enter your MT5 credentials in Settings." -ForegroundColor Green
Write-Host ""

$confirm = Read-Host "Continue? (y/n)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Cancelled." -ForegroundColor Red
    exit 0
}

Write-Host ""
Write-Host "Cleaning logs..." -ForegroundColor Yellow

$filesToDelete = @(
    "events.jsonl",
    "trades.db",
    "state_snapshot.json"
)

$deletedCount = 0

foreach ($fileName in $filesToDelete) {
    $filePath = Join-Path $LogsDir $fileName
    if (Test-Path $filePath) {
        try {
            Remove-Item $filePath -Force
            Write-Host "  Deleted: $fileName" -ForegroundColor Gray
            $deletedCount++
        } catch {
            Write-Host "  Error deleting ${fileName}: $_" -ForegroundColor Red
        }
    }
}

$logFiles = Get-ChildItem -Path $LogsDir -Filter "*.log" -ErrorAction SilentlyContinue
foreach ($file in $logFiles) {
    try {
        Remove-Item $file.FullName -Force
        Write-Host "  Deleted: $($file.Name)" -ForegroundColor Gray
        $deletedCount++
    } catch {
        Write-Host "  Error deleting $($file.Name): $_" -ForegroundColor Red
    }
}

$configFile = Join-Path $ConfigDir "default.yaml"
if (Test-Path $configFile) {
    Write-Host ""
    Write-Host "Resetting MT5 credentials in config..." -ForegroundColor Yellow
    
    $content = Get-Content $configFile -Raw -Encoding UTF8
    
    $content = $content -replace 'login:\s*\d+', 'login: 0'
    $content = $content -replace 'password:\s*"[^"]*"', 'password: ""'
    $content = $content -replace 'server:\s*"[^"]*"', 'server: ""'
    $content = $content -replace 'bot_token:\s*"[^"]*"', 'bot_token: ""'
    $content = $content -replace 'chat_id:\s*"[^"]*"', 'chat_id: ""'
    
    Set-Content $configFile $content -Encoding UTF8 -NoNewline
    Write-Host "  Credentials cleared" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Done!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Deleted files: $deletedCount" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Run run_gui.ps1"
Write-Host "  2. Open Settings and enter your MT5 credentials"
Write-Host "  3. Click Start to begin trading"
Write-Host ""

Read-Host "Press Enter to exit"
