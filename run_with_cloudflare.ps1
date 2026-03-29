$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

# 1. Load .env
if (Test-Path "$ScriptDir\.env") {
    Get-Content "$ScriptDir\.env" | ForEach-Object {
        if ($_ -match "^\s*([^#=\s][^=]*)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
    Write-Host "Loaded .env" -ForegroundColor Gray
} else {
    Write-Host ".env not found!" -ForegroundColor Red; pause; exit 1
}

$port = if ($env:API_PORT) { $env:API_PORT } else { "8100" }

# 2. Start SSH tunnel via serveo.net
Write-Host ""
Write-Host "Starting SSH tunnel (serveo.net) on port $port ..." -ForegroundColor Cyan

$sshLog = "$env:TEMP\scalper_ssh.log"
$sshErrLogPath = "$env:TEMP\scalper_ssh_err.log"
# Kill any leftover SSH tunnel processes first
Get-Process ssh -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
if (Test-Path $sshLog) { Remove-Item $sshLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $sshErrLogPath) { Remove-Item $sshErrLogPath -Force -ErrorAction SilentlyContinue }

$sshProcess = Start-Process `
    -FilePath "ssh" `
    -ArgumentList "-o","StrictHostKeyChecking=no","-o","ServerAliveInterval=30","-R","80:localhost:$port","serveo.net" `
    -RedirectStandardOutput $sshLog `
    -RedirectStandardError $sshErrLogPath `
    -NoNewWindow -PassThru

# Wait for tunnel URL
$tunnelUrl = $null
$waited = 0
Write-Host "Waiting for tunnel URL" -NoNewline -ForegroundColor Gray
while ($waited -lt 20) {
    Start-Sleep -Seconds 1; $waited++
    Write-Host "." -NoNewline -ForegroundColor Gray
    foreach ($logFile in @($sshLog, $sshErrLogPath)) {
        if (Test-Path $logFile) {
            $content = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
            if ($content -match 'Forwarding HTTP traffic from (https://\S+)') {
                $tunnelUrl = $Matches[1]; break
            }
            if ($content -match '(https://[a-z0-9A-Z_-]+\.serveo(?:usercontent)?\.(?:net|com))') {
                $tunnelUrl = $Matches[1]; break
            }
        }
    }
}
Write-Host ""

if (-not $tunnelUrl) {
    Write-Host "Could not get tunnel URL. Check if serveo.net is accessible." -ForegroundColor Yellow
    Write-Host "Bot will still work without Mini App button." -ForegroundColor Gray
    $sshProcess = $null
} else {
    $env:API_URL = $tunnelUrl
    Write-Host "Tunnel: $tunnelUrl" -ForegroundColor Green
    if (-not $env:WEBAPP_URL) {
        $env:WEBAPP_URL = "$tunnelUrl/webapp/index.html"
        Write-Host "WEBAPP_URL = $env:WEBAPP_URL" -ForegroundColor Yellow
    } else {
        Write-Host "WEBAPP_URL (GitHub Pages): $env:WEBAPP_URL" -ForegroundColor Green
        Write-Host "API_URL   (Tunnel):        $tunnelUrl" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Starting Telegram service..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop."
Write-Host ""

try {
    & "$ScriptDir\venv\Scripts\python.exe" "$ScriptDir\run_tg_service.py"
} finally {
    if ($sshProcess -and -not $sshProcess.HasExited) {
        $sshProcess.Kill()
        Write-Host "SSH tunnel stopped." -ForegroundColor Gray
    }
}