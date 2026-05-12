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
    [switch]$LocalMode,      # shorthand: audio + vision + TTS use laptop hardware
    [switch]$LocalAudio,     # audio service uses laptop microphone
    [switch]$LocalVision,    # vision service uses laptop webcam (BLOCKS browser enrollment on Windows -- the OS doesn't share cameras)
    [switch]$LocalTts,       # TTS service plays through laptop speakers
    [switch]$RelayTts,       # force TTS audio to the Pi relay instead
    [switch]$NoFakeRedis,    # talk to a real Redis (default: Memurai on 127.0.0.1:6379)
    [switch]$WithMl,         # include the [ml] extras (insightface/torch/whisper/yolo) -- needs MSVC Build Tools
    [switch]$Reinstall,      # force pip install -e even if the package is already present (slow on Windows)
    [string]$PythonExe       # explicit Python interpreter; default = `py -3.11`
)
if ($LocalMode) { $LocalAudio = $true; $LocalVision = $true; $LocalTts = $true }

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

# Kill any palantir-* services left over from a previous launcher run.
# Otherwise pip install will fail with WinError 32 (file in use) when it
# tries to overwrite Scripts\palantir-*.exe.
$leftover = Get-Process -Name 'palantir-*' -ErrorAction SilentlyContinue
if ($leftover) {
    Write-Host ("[!] Killing leftover palantir-* services ({0} processes)..." -f $leftover.Count) -ForegroundColor Yellow
    $leftover | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

# Skip pip install when palantir is already installed -- the editable install
# means source changes are picked up live, so a reinstall on every launch is
# pure overhead (and on Windows, races leftover .exe locks).  Pass -Reinstall
# to force a refresh after pyproject.toml changes.
$skipPipInstall = $false
if (-not $Reinstall) {
    $sentinelExe = Join-Path $Venv "Scripts\palantir-brain.exe"
    if (Test-Path $sentinelExe) {
        $importOk = & $VenvPython -c "import palantir; print('ok')" 2>$null
        if ($importOk -eq "ok") {
            Write-Host "[2/4] palantir already installed; skipping pip install (use -Reinstall to force refresh)" -ForegroundColor DarkGreen
            $skipPipInstall = $true
        }
    }
}

if (-not $skipPipInstall) {
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
  - A previous palantir-*.exe is still running and locking the file.
    The launcher tries to kill leftovers up front, but if a stray service
    survived, run `Stop-Process -Name palantir-* -Force` and retry.
"@
        throw "pip install -e $pkgSpec failed"
    }
}

# 2. Frontend bundle.  The web service mounts frontend/dist/ at "/", so
# without a build the dashboard returns {"detail": "Not Found"}.  Install
# Node automatically if winget is available and node is missing.
$Node = Get-Command node -ErrorAction SilentlyContinue
if (-not $Node) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "[3/4] node not found; installing Node.js LTS via winget..." -ForegroundColor Cyan
        winget install --silent -e --id OpenJS.NodeJS.LTS `
            --accept-source-agreements --accept-package-agreements | Out-Null
        # winget installs node to a fixed path on Windows; add it to this
        # session's PATH so we can use it without restarting the shell.
        $nodeDir = "C:\Program Files\nodejs"
        if (Test-Path (Join-Path $nodeDir "node.exe")) {
            $env:PATH = "$nodeDir;$env:PATH"
            $Node = Get-Command node -ErrorAction SilentlyContinue
        }
    }
}

$frontendDist = Join-Path $RepoRoot "frontend\dist"
if ($Node) {
    Write-Host "[3/4] Building frontend..." -ForegroundColor Cyan
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        if (-not (Test-Path "node_modules")) { npm install --silent }
        npm run build --silent
    } finally { Pop-Location }
} elseif (Test-Path $frontendDist) {
    Write-Host "[3/4] node not found; using existing $frontendDist" -ForegroundColor Yellow
} else {
    Write-Host "[3/4] node not found AND no frontend/dist -- dashboard will 404." -ForegroundColor Red
    Write-Host "       Install Node.js LTS manually:" -ForegroundColor Red
    Write-Host "         winget install -e --id OpenJS.NodeJS.LTS" -ForegroundColor Yellow
    Write-Host "       Then re-run this script." -ForegroundColor Red
}

# 3. Data dirs + dev env
foreach ($d in @("enrollments", "models", "backups", "tls")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
}
if (-not $AuthToken) { $AuthToken = "devtoken" }

Write-Host "[4/4] Starting services..." -ForegroundColor Cyan
$audioMode  = if ($LocalAudio)  { "local" } else { "relay" }
$visionMode = if ($LocalVision) { "local" } else { "relay" }
$ttsMode    = if ($RelayTts) { "relay" } elseif ($LocalTts -or $LocalAudio) { "local" } else { "relay" }
$relayDesc  = "audio=$audioMode, vision=$visionMode, tts=$ttsMode"
# Redis: every service needs to share a real Redis-protocol broker.  An
# in-process fakeredis is per-process, and fakeredis.TcpFakeServer's pub/sub
# is broken on Windows (no fcntl -> handler thread blocks in readline() and
# never delivers pub/sub messages), so heartbeats and wake-word events would
# never cross the process boundary.  Default = Memurai on 127.0.0.1:6379.
# Pass -NoFakeRedis to honor an externally-set REDIS_URL instead.
$redisDesc = if ($NoFakeRedis) { "honoring `$env:REDIS_URL" } else { "memurai on 127.0.0.1:6379" }

# TLS: set the cert + key paths so the web service auto-generates a
# self-signed cert in .dev-data/tls/ on first start.  Without this,
# the web service serves plain http and the browser blocks
# getUserMedia() on non-localhost origins.
$tlsCert = Join-Path $DataDir "tls\cert.pem"
$tlsKey  = Join-Path $DataDir "tls\key.pem"

Write-Host ("  auth token:    " + $AuthToken)
Write-Host ("  data dir:      " + $DataDir)
Write-Host ("  relay mode:    " + $relayDesc)
Write-Host ("  redis:         " + $redisDesc)
Write-Host ("  tls cert:      " + $tlsCert)

$envOverrides = @{
    PALANTIR_ENV             = "development"
    PALANTIR_AUTH_TOKEN      = $AuthToken
    PALANTIR_DB_PATH         = (Join-Path $DataDir "palantir.db")
    PALANTIR_ENROLLMENT_PATH = (Join-Path $DataDir "enrollments")
    PALANTIR_TLS_CERT_FILE   = $tlsCert
    PALANTIR_TLS_KEY_FILE    = $tlsKey
}
if (-not $NoFakeRedis) {
    # Point every service at Memurai (free Redis-compatible Windows server,
    # default port 6379).  Crucially we do NOT set PALANTIR_REDIS_FAKE=1 --
    # that triggers the in-process fakeredis path, which gives each service
    # its OWN keyspace and silently breaks all inter-service messaging.
    $envOverrides["REDIS_URL"] = "redis://127.0.0.1:6379/0"
}
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
    if (-not $NoFakeRedis) {
        # Memurai is a Windows service that auto-starts; we just verify the
        # port is reachable before launching the services.  If it's missing
        # the services would spew connection-refused errors and the
        # dashboard would still show every service offline.
        $reachable = $false
        $deadline = (Get-Date).AddSeconds(5)
        while ((Get-Date) -lt $deadline) {
            try {
                $tc = New-Object System.Net.Sockets.TcpClient
                $tc.Connect("127.0.0.1", 6379)
                $tc.Close()
                $reachable = $true
                break
            } catch {
                Start-Sleep -Milliseconds 200
            }
        }
        if (-not $reachable) {
            Write-Host ""
            Write-Host "Cannot reach Redis on 127.0.0.1:6379." -ForegroundColor Red
            Write-Host "Install Memurai (free, native Windows Redis):" -ForegroundColor Red
            Write-Host "  winget install -e --id Memurai.MemuraiDeveloper" -ForegroundColor Yellow
            Write-Host "Or pass -NoFakeRedis and set `$env:REDIS_URL to your own broker." -ForegroundColor Yellow
            throw "Redis broker unreachable -- aborting."
        }
        Write-Host ("  redis broker:  reachable on 127.0.0.1:6379") -ForegroundColor DarkGreen
    }

    foreach ($svc in $services) {
        $exe = Join-Path $Venv "Scripts\$svc.exe"
        if (-not (Test-Path $exe)) {
            Write-Warning "missing entry point: $exe (did pip install run?)"
            continue
        }
        # Set the per-service relay mode just before spawning, so
        # palantir-audio sees `local` (laptop mic) while palantir-vision
        # sees `relay` (no cv2.VideoCapture grab; browser can use the cam).
        switch ($svc) {
            "palantir-audio"  { [Environment]::SetEnvironmentVariable("PALANTIR_RELAY_MODE", $audioMode,  "Process") }
            "palantir-vision" { [Environment]::SetEnvironmentVariable("PALANTIR_RELAY_MODE", $visionMode, "Process") }
            "palantir-tts"    { [Environment]::SetEnvironmentVariable("PALANTIR_RELAY_MODE", $ttsMode,    "Process") }
            default           { [Environment]::SetEnvironmentVariable("PALANTIR_RELAY_MODE", "relay",     "Process") }
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
    Write-Host "Dashboard: https://localhost:8080  (self-signed cert -- accept the browser warning)"
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
