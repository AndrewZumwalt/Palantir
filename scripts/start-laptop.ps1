# Palantir laptop-side launcher (Windows / PowerShell).
#
# Spawns the six service processes in relay mode (the laptop expects to
# receive sensor data from a Pi via /relay/ws -- the audio + vision
# captures subscribe to Redis instead of opening local hardware).
#
# Run from the repo root:
#     powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1
#
# Ctrl-C terminates all child processes.  Logs are interleaved in this
# console; use the dashboard at https://localhost:8080 for the structured
# view once the web service is up.

[CmdletBinding()]
param(
    [string]$AuthToken    = $env:PALANTIR_AUTH_TOKEN,
    [string]$AnthropicKey = $env:ANTHROPIC_API_KEY,
    [string]$GroqKey      = $env:GROQ_API_KEY,
    [string]$DataDir,
    [switch]$LocalMode,   # use local mic/cam on the laptop instead of waiting for Pi
    [switch]$NoFakeRedis  # talk to a real Redis (default: in-process fakeredis)
)

$ErrorActionPreference = "Stop"

# Resolve the script's own directory.  We can't rely on $PSScriptRoot in
# the param() block -- on Windows PowerShell 5.1 it's empty there in some
# invocation paths.  $PSCommandPath is reliably the full path of the
# script being executed.
$ScriptPath = $PSCommandPath
if (-not $ScriptPath) { $ScriptPath = $MyInvocation.MyCommand.Path }
if (-not $ScriptPath) {
    throw "Cannot determine script path -- run this file via -File, e.g.`n  powershell -ExecutionPolicy Bypass -File <full-path>\start-laptop.ps1"
}
$ScriptDir = Split-Path -Parent $ScriptPath
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..")).Path
if (-not $DataDir) { $DataDir = Join-Path $RepoRoot ".dev-data" }

# 1. venv
$Venv = Join-Path $RepoRoot ".venv"
if (-not (Test-Path $Venv)) {
    Write-Host "[1/4] Creating venv..." -ForegroundColor Cyan
    python -m venv $Venv
    & "$Venv\Scripts\python.exe" -m pip install --upgrade --quiet pip
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"

Write-Host "[2/4] Installing Python deps..." -ForegroundColor Cyan
& $VenvPython -m pip install --quiet -e "$RepoRoot[ml,dev]"

# 2. Frontend bundle (only if node is available)
$Node = Get-Command node -ErrorAction SilentlyContinue
if ($Node) {
    Write-Host "[3/4] Building frontend..." -ForegroundColor Cyan
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        if (-not (Test-Path "node_modules")) { npm install --silent }
        npm run build --silent
    } finally { Pop-Location }
} else {
    Write-Host "[3/4] node not found; serving whatever's already in frontend/dist" -ForegroundColor Yellow
}

# 3. Data dirs + dev env
foreach ($d in @("enrollments", "models", "backups", "tls")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
}
if (-not $AuthToken) { $AuthToken = "devtoken" }

Write-Host "[4/4] Starting services..." -ForegroundColor Cyan
$relayDesc = if ($LocalMode) { "local (laptop hardware)" } else { "relay (waiting for Pi)" }
$redisDesc = if ($NoFakeRedis) { "real (REDIS_URL or unix:///var/run/redis/redis.sock)" } else { "in-process fakeredis" }
$relayMode = if ($LocalMode) { "local" } else { "relay" }
Write-Host ("  auth token:    " + $AuthToken)
Write-Host ("  data dir:      " + $DataDir)
Write-Host ("  relay mode:    " + $relayDesc)
Write-Host ("  redis:         " + $redisDesc)

$envOverrides = @{
    PALANTIR_ENV             = "development"
    PALANTIR_AUTH_TOKEN      = $AuthToken
    PALANTIR_DB_PATH         = (Join-Path $DataDir "palantir.db")
    PALANTIR_ENROLLMENT_PATH = (Join-Path $DataDir "enrollments")
    PALANTIR_RELAY_MODE      = $relayMode
}
if (-not $NoFakeRedis) { $envOverrides["PALANTIR_REDIS_FAKE"] = "1" }
if ($AnthropicKey)     { $envOverrides["ANTHROPIC_API_KEY"]   = $AnthropicKey }
if ($GroqKey)          { $envOverrides["GROQ_API_KEY"]        = $GroqKey }

# Each service is a separate process so a crash in one (e.g. ML model
# load) doesn't kill the others.  We launch them via the venv's
# generated console scripts.
$services = @(
    "palantir-eventlog",
    "palantir-brain",
    "palantir-tts",
    "palantir-vision",
    "palantir-audio",
    "palantir-web"
)

$processes = @()
$priorEnv = @{}
foreach ($k in $envOverrides.Keys) {
    $priorEnv[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
    [Environment]::SetEnvironmentVariable($k, $envOverrides[$k], "Process")
}
try {
    foreach ($svc in $services) {
        $exe = Join-Path $Venv "Scripts\$svc.exe"
        if (-not (Test-Path $exe)) {
            Write-Warning "missing entry point: $exe (did pip install run?)"
            continue
        }
        Write-Host ("  -> $svc") -ForegroundColor DarkCyan
        $p = Start-Process -FilePath $exe -PassThru -NoNewWindow `
            -WorkingDirectory $RepoRoot `
            -RedirectStandardOutput (Join-Path $DataDir "$svc.out.log") `
            -RedirectStandardError  (Join-Path $DataDir "$svc.err.log")
        $processes += @{ name = $svc; proc = $p }
    }

    Write-Host ""
    Write-Host "All six services launched.  Logs streaming to $DataDir\*.log"
    Write-Host "Dashboard: https://localhost:8080"
    Write-Host "Press Ctrl-C to stop." -ForegroundColor Yellow

    while ($true) {
        $alive = $false
        foreach ($s in $processes) {
            if (-not $s.proc.HasExited) { $alive = $true } else {
                Write-Warning ("  service $($s.name) exited (code $($s.proc.ExitCode))")
            }
        }
        if (-not $alive) {
            Write-Warning "All services have exited."
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "Shutting down..." -ForegroundColor Cyan
    foreach ($s in $processes) {
        if (-not $s.proc.HasExited) {
            try { Stop-Process -Id $s.proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    foreach ($k in $priorEnv.Keys) {
        [Environment]::SetEnvironmentVariable($k, $priorEnv[$k], "Process")
    }
}
