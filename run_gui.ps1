#Requires -Version 5.1
<#
.SYNOPSIS
    Launch XAUUSD Scalper Bot GUI with preflight checks.
.DESCRIPTION
    Creates/activates venv, installs deps, validates MT5 terminal presence,
    runs Python preflight check, then launches PySide6 GUI.
#>

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RootDir "venv"
$RequirementsFile = Join-Path $RootDir "requirements.txt"
$RequirementsStampFile = Join-Path $VenvDir ".requirements.sha256"
$PreflightScript = Join-Path $RootDir "app\src\preflight.py"
$MainScript = Join-Path $RootDir "app\src\main.py"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " XAUUSD Scalper Bot - Startup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── 1. Python 3.11+ check ────────────────────────────────────────────────────
Write-Host "`n[1/6] Checking Python version..." -ForegroundColor Yellow
$pythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $pythonCmd = $candidate
                Write-Host "   Found: $ver" -ForegroundColor Green
                break
            }
        }
    } catch { }
}
if (-not $pythonCmd) {
    Write-Host "   ERROR: Python 3.11+ not found in PATH." -ForegroundColor Red
    Write-Host "   Install from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# ── 2. venv create/activate ───────────────────────────────────────────────────
Write-Host "`n[2/6] Setting up virtual environment at $VenvDir ..." -ForegroundColor Yellow
if (-not (Test-Path $VenvDir)) {
    Write-Host "   Creating venv..." -ForegroundColor DarkYellow
    & $pythonCmd -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Host "   ERROR: venv creation failed." -ForegroundColor Red; exit 1 }
}
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "   ERROR: venv Python executable not found at $PythonExe" -ForegroundColor Red
    exit 1
}
Write-Host "   venv OK: $PythonExe" -ForegroundColor Green

# ── 3. Install / upgrade dependencies ────────────────────────────────────────
Write-Host "`n[3/6] Checking/installing dependencies..." -ForegroundColor Yellow
if (-not (Test-Path $RequirementsFile)) {
    Write-Host "   ERROR: requirements.txt not found at $RequirementsFile" -ForegroundColor Red
    exit 1
}

$requirementsHash = (Get-FileHash $RequirementsFile -Algorithm SHA256).Hash
$cachedRequirementsHash = ""
if (Test-Path $RequirementsStampFile) {
    $cachedRequirementsHash = (Get-Content $RequirementsStampFile -Raw).Trim()
}

$dependencyCheckCode = @"
import importlib.util
import sys

modules = [
    'MetaTrader5',
    'PySide6',
    'numpy',
    'pandas',
    'sklearn',
    'yaml',
    'pydantic',
    'httpx',
    'aiohttp',
    'requests',
    'sqlalchemy',
    'alembic',
    'structlog',
    'orjson',
]

missing = [name for name in modules if importlib.util.find_spec(name) is None]
if missing:
    print('MISSING:' + ','.join(missing))
    sys.exit(1)
print('OK')
"@

& $PythonExe -c $dependencyCheckCode *> $null
$dependenciesImportable = ($LASTEXITCODE -eq 0)

$needInstall = -not (Test-Path $RequirementsStampFile) -or ($cachedRequirementsHash -ne $requirementsHash)
if ($needInstall -and $dependenciesImportable) {
    Write-Host "   Required modules already import correctly; caching requirements hash and skipping pip install." -ForegroundColor Green
    Set-Content -Path $RequirementsStampFile -Value $requirementsHash -Encoding ASCII
} elseif ($needInstall) {
    Write-Host "   Requirements changed or first launch. Installing packages..." -ForegroundColor DarkYellow
    Write-Host "   This can take a few minutes on the first run." -ForegroundColor DarkYellow

    & $PythonExe -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   ERROR: pip upgrade failed." -ForegroundColor Red
        exit 1
    }

    & $PythonExe -m pip install --disable-pip-version-check -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   ERROR: pip install failed. Check requirements.txt and network connectivity." -ForegroundColor Red
        exit 1
    }

    Set-Content -Path $RequirementsStampFile -Value $requirementsHash -Encoding ASCII
    Write-Host "   Dependencies installed and cached." -ForegroundColor Green
} else {
    Write-Host "   Dependencies already match requirements.txt; skipping pip install." -ForegroundColor Green
}

# ── 4. MT5 terminal presence check ───────────────────────────────────────────
Write-Host "`n[4/6] Checking MetaTrader5 terminal presence..." -ForegroundColor Yellow
$MT5Paths = @(
    "C:\Program Files\MetaTrader 5\terminal64.exe",
    "C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    "$env:APPDATA\MetaQuotes\Terminal",
    "$env:LOCALAPPDATA\Programs\MetaTrader 5\terminal64.exe"
)
$mt5Found = $false
foreach ($p in $MT5Paths) {
    if (Test-Path $p) {
        Write-Host "   Found MT5 at: $p" -ForegroundColor Green
        $mt5Found = $true
        break
    }
}
# Also check running processes
$mt5Process = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if ($mt5Process) {
    Write-Host "   MT5 terminal64.exe is currently RUNNING (PID $($mt5Process.Id))" -ForegroundColor Green
    $mt5Found = $true
}
if (-not $mt5Found) {
    Write-Host "   WARNING: MetaTrader5 terminal executable not found in standard locations." -ForegroundColor Yellow
    Write-Host "   The bot uses the MetaTrader5 Python package which requires the terminal." -ForegroundColor Yellow
    Write-Host "   Download from: https://www.metatrader5.com/en/download" -ForegroundColor Yellow
    $ans = Read-Host "   Continue anyway? [y/N]"
    if ($ans -notmatch "^[yY]") { exit 1 }
}

# ── 5. Python preflight validation ───────────────────────────────────────────
Write-Host "`n[5/6] Running Python preflight checks..." -ForegroundColor Yellow
if (Test-Path $PreflightScript) {
    & $PythonExe $PreflightScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   PREFLIGHT FAILED. Resolve above errors before starting." -ForegroundColor Red
        exit 1
    }
    Write-Host "   Preflight OK." -ForegroundColor Green
} else {
    Write-Host "   WARNING: preflight.py not found, skipping." -ForegroundColor Yellow
}

# ── 6. Config snapshot ───────────────────────────────────────────────────────
$configSnapshotCode = @'
import yaml, hashlib
with open(r'config/default.yaml') as f:
    raw = f.read()
cfg = yaml.safe_load(raw)
h = hashlib.md5(raw.encode()).hexdigest()[:8]
tr = cfg['trailing']
be = cfg['breakeven']
en = cfg['entry']
print('  %-16s %s' % ('Config hash:', h))
print('  %-16s вход при +%g pts,  SL в %g pts от пика,  шаг %g pts' % ('Trailing:', tr['trail_activation_points'], tr['trail_stop_points'], tr['trail_step_points']))
print('  %-16s BE при $%g прибыли,  SL на $%g' % ('Breakeven:', be['be_activation_usd'], be['be_stop_usd']))
print('  %-16s макс смещение %g pts,  минимум %g pts' % ('Ордера:', en['offset_abs_max_points'], en['min_total_offset_points']))
print('  %-16s порог %g pts,  коэф %g' % ('Capture mode:', en['impulse_capture_floor_pts'], en['impulse_capture_spread_mult']))
print('  %-16s гистерезис %g pts,  задержка %g мс' % ('Обновление:', en['rearm_hysteresis_pts'], en['min_order_age_ms']))
'@
Set-Location $RootDir
Write-Host ""
Write-Host "  +----------------------------------------------------+" -ForegroundColor DarkCyan
Write-Host "  |        XAUUSD SCALPER  --  CONFIG PARAMS         |" -ForegroundColor Cyan
Write-Host "  +----------------------------------------------------+" -ForegroundColor DarkCyan
& $PythonExe -c $configSnapshotCode
Write-Host "  +----------------------------------------------------+" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Launching GUI..." -ForegroundColor Green
Write-Host ""
& $PythonExe $MainScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nApplication exited with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
