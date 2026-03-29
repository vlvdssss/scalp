<#
.SYNOPSIS
    Запускает Cloudflare Quick Tunnel + Scalper Telegram Service.
    Cloudflare автоматически предоставляет бесплатный HTTPS-URL — ngrok не нужен.

.NOTES
    Требует cloudflared.exe в PATH или в папке C:\Scalper.
    Скачать: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    (одиночный .exe файл, регистрация не нужна для quick tunnels)

    Запускать вместо run_gui.ps1 — одновременно нельзя!
#>

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

# ── 1. Проверяем cloudflared ──────────────────────────────────────────────────
$cfExe = $null
if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    $cfExe = "cloudflared"
} elseif (Test-Path "$ScriptDir\cloudflared.exe") {
    $cfExe = "$ScriptDir\cloudflared.exe"
} else {
    Write-Host ""
    Write-Host "  cloudflared.exe не найден!" -ForegroundColor Red
    Write-Host "  Скачай: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" -ForegroundColor Yellow
    Write-Host "  Положи cloudflared.exe в папку C:\Scalper и запусти снова." -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

# ── 2. Загружаем .env ─────────────────────────────────────────────────────────
if (Test-Path "$ScriptDir\.env") {
    Get-Content "$ScriptDir\.env" | ForEach-Object {
        if ($_ -match "^\s*([^#=\s][^=]*)=(.*)$") {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
    Write-Host "Loaded .env" -ForegroundColor Gray
} else {
    Write-Host ".env not found — set BOT_TOKEN manually!" -ForegroundColor Red
    pause
    exit 1
}

$port    = if ($env:API_PORT) { $env:API_PORT } else { "8100" }
$logFile = "$env:TEMP\scalper_cloudflared.log"

# ── 3. Запускаем cloudflared в фоне ──────────────────────────────────────────
Write-Host ""
Write-Host "Starting Cloudflare tunnel on port $port ..." -ForegroundColor Cyan

if (Test-Path $logFile) { Remove-Item $logFile -Force }

$cfProcess = Start-Process `
    -FilePath $cfExe `
    -ArgumentList "tunnel", "--url", "http://localhost:$port" `
    -RedirectStandardError $logFile `
    -NoNewWindow `
    -PassThru

# ── 4. Ждём URL от cloudflare (до 30 сек) ────────────────────────────────────
$cfUrl   = $null
$waited  = 0
$timeout = 30

Write-Host "Waiting for tunnel URL" -NoNewline -ForegroundColor Gray
while ($waited -lt $timeout) {
    Start-Sleep -Seconds 1
    $waited++
    Write-Host "." -NoNewline -ForegroundColor Gray

    if (Test-Path $logFile) {
        $content = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        if ($content -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
            $cfUrl = $Matches[0]
            break
        }
    }
}
Write-Host ""

if (-not $cfUrl) {
    Write-Host "Could not detect tunnel URL. Check log: $logFile" -ForegroundColor Yellow
    Write-Host "Continuing without WEBAPP tunnel URL..." -ForegroundColor Yellow
} else {
    Write-Host "Cloudflare tunnel: $cfUrl" -ForegroundColor Green

    # Передаём API_URL в Python-сервис
    $env:API_URL = $cfUrl

    # Если WEBAPP_URL не задан вручную (или это старый cloudflare URL) — берём тоннельный
    if (-not $env:WEBAPP_URL) {
        $env:WEBAPP_URL = "$cfUrl/webapp/index.html"
        Write-Host "WEBAPP_URL = $env:WEBAPP_URL" -ForegroundColor Yellow
        Write-Host "(Set WEBAPP_URL in .env to use GitHub Pages instead)" -ForegroundColor Gray
    } else {
        Write-Host "WEBAPP_URL (GitHub Pages): $env:WEBAPP_URL" -ForegroundColor Green
        Write-Host "API_URL   (Cloudflare):    $cfUrl" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Starting Telegram service..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

# ── 5. Запускаем Python-сервис ─────────────────────────────────────────────────
try {
    & "$ScriptDir\venv\Scripts\python.exe" "$ScriptDir\run_tg_service.py"
} finally {
    # Останавливаем cloudflared при выходе
    if ($cfProcess -and -not $cfProcess.HasExited) {
        $cfProcess.Kill()
        Write-Host "Cloudflare tunnel stopped." -ForegroundColor Gray
    }
}
