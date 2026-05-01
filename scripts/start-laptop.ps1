# Palantir laptop-side launcher (Windows / PowerShell).
#
# Spawns the six service processes in relay mode (the laptop expects to
# receive sensor data from a Pi via /relay/ws -- the audio + vision
# captures subscribe to Redis instead of opening local hardware).
#
# Prereqs:
#   * Python 3.11 (winget install -e --id Python.Python.3.11).  Many ML
#     wheels still don't ship for 3.12/3.13 on Windows.
#   * Optional, only with -WithMl: Microsoft C++ Build Tools, because
#     insightface and friends build a Cython extension on install.
#     https://visualstudio.microsoft.com/visual-cpp-build-tools/
#
# Run from the repo root:
#     powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1
#
# Add -WithMl once Build Tools are installed for face/object detection:
#     powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1 -WithMl
#
# Ctrl-C terminates all child processes.  Logs stream to .\.dev-data\*.log;
# use the dashboard at https://localhost:8080 for the structured view
# once the web service is up.

[CmdletBinding()]
param(
    [string]$AuthToken    = $env:PALANTIR_AUTH_TOKEN,
    [string]$AnthropicKey = $env:ANTHROPIC_API_KEY,
    [string]$GroqKey      = $env:GROQ_API_KEY,
    [string]$DataDir,
    [switch]$LocalMode,    # use local mic/cam on the laptop instead of waiting for Pi
    [switch]$NoFakeRedis,  # talk to a real Redis (default: in-process fakeredis)
    [switch]$WithMl,       # include the [ml] extras (insightface/torch/whisper/yolo) -- needs MSVC Build Tools
    [string]$PythonExe     # explicit Python interpreter; default = `py -3.11`
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

# 1. venv (must be Python 3.11 -- many ML wheels lag for 3.12+)
$Venv = Join-Path $RepoRoot ".venv"

function Resolve-Python311 {
    param([string]$Hint)
    if ($Hint) {
        if (-not (Test-Path $Hint)) { throw "PythonExe '$Hint' does not exist" }
        return $Hint
    }
    # Prefer the Python launcher if installed (winget put 3.11 here).
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $candidate = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
            if ($candidate -and (Test-Path $candidate)) { return $candidate }
        } catch {}
    }
    # Fall back to `python3.11` on PATH.
    $direct = Get-Command python3.11 -ErrorAction SilentlyContinue
    if ($direct) { return $direct.Source }
    throw "Could not find Python 3.11.  Install it (winget install -e --id Python.Python.3.11) or pass -PythonExe <path>."
}

if (-not (Test-Path $Venv)) {
    $py311 = Resolve-Python311 -Hint $PythonExe
    Write-Host "[1/4] Creating venv with $py311 ..." -ForegroundColor Cyan
    & $py311 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    & "$Venv\Scripts\python.exe" -m pip install --upgrade --quiet pip
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Venv looks broken ($VenvPython missing).  Delete $Venv and re-run."
}
$venvVer = (& $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($venvVer -ne "3.11") {
    Write-Host ""
    Write-Host "Existing venv runs Python $venvVer; this project needs 3.11." -ForegroundColor Red
    Write-Host "Delete the venv and re-run:" -ForegroundColor Red
    Write-Host "    Remove-Item -Recurse -Force '$Venv'" -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\scripts\start-laptop.ps1" -ForegroundColor Yellow
    throw "wrong Python version in $Venv (got $venvVer, need 3.11)"
}

# By default just install the base + dev extras -- enough to launch the
# six services in relay mode without the heavy ML stack.  Pass -WithMl
# once you have MSVC Build Tools installed and want face/object detection.
if ($WithMl) {
    $pkgSpec   = "$RepoRoot" + "[ml,dev]"
    $pkgFlavor = "with [ml]"
} else {
    $pkgSpec   = "$RepoRoot" + "[dev]"
    $pkgFlavor = "core only"
}

Write-Host ("[2/4] Installing Python deps ({0}) ..." -f $pkgFlavor) -ForegroundColor Cyan
& $VenvPython -m pip install --quiet -e $pkgSpec
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Warning @"
pip install failed.  Common Windows causes:
  - insightface / openwakeword need a C++ compiler.  Install
    "Microsoft C++ Build Tools" (https://visualstudio.microsoft.com/visual-cpp-build-tools/)
    OR re-run without -WithMl to skip the heavy ML stack.
  - Wrong Python version.  This script requires Python 3.11; delete
    $Venv and re-run if it picked up a different one.
"@
    throw "pip install -e $pkgSpec failed"
}

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

    $reported = @{}
    while ($true) {
        $alive = $false
        foreach ($s in $processes) {
            if (-not $s.proc.HasExited) {
                $alive = $true
                continue
            }
            if ($reported[$s.name]) { continue }
            $reported[$s.name] = $true
            $code = $s.proc.ExitCode
            if ($null -eq $code) { $code = "?" }
            Write-Warning ("service {0} exited (code {1})" -f $s.name, $code)
            # Tail the last few lines of the service's stderr to surface
            # the real failure (import error, missing key, etc.).
            $errLog = Join-Path $DataDir ("{0}.err.log" -f $s.name)
            if ((Test-Path $errLog) -and (Get-Item $errLog).Length -gt 0) {
                Write-Host ("---- last 20 lines of {0} ----" -f $errLog) -ForegroundColor DarkGray
                Get-Content $errLog -Tail 20 | ForEach-Object {
                    Write-Host ("  | {0}" -f $_) -ForegroundColor DarkGray
                }
                Write-Host "----" -ForegroundColor DarkGray
            } else {
                Write-Host ("  (no stderr captured at {0})" -f $errLog) -ForegroundColor DarkGray
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
