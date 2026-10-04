# 24/7 Watchdog for Harness Farm and Bifrost.
# Monitors farm run (command consumer / gateway) and Bifrost health.
# If either service is unhealthy for >= 60s, restarts it, logs, and sends a Telegram alert.
# Usage:
#   powershell -File scripts/farm-watchdog.ps1 [-TestMode] [-CheckIntervalSeconds 15]

param(
    [switch]$TestMode,
    [int]$CheckIntervalSeconds = 15,
    [int]$UnhealthyThresholdSeconds = 60,
    [string]$BifrostUrl = "http://127.0.0.1:8080",
    [string]$FarmHeartbeatFile = "",
    [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$FarmDataDir = if ($env:FARM_DATA_DIR) { $env:FARM_DATA_DIR } else { "D:\farm-data" }
if (-not $FarmHeartbeatFile) {
    $FarmHeartbeatFile = Join-Path $FarmDataDir "heartbeats\command_consumer.json"
}
if (-not $LogFile) {
    $LogDir = Join-Path $FarmDataDir "logs"
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $LogFile = Join-Path $LogDir "watchdog.log"
}

function Write-WatchdogLog([string]$message) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$ts] $message"
    Write-Host $line
    try {
        Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    } catch {}
}

function Send-WatchdogAlert([string]$message) {
    Write-WatchdogLog "ALERT: $message"
    # Attempt 'farm alert send' CLI if available, else python dispatch
    $cmd = "import asyncio; from farm.manager.telegram import send_telegram_alert; asyncio.run(send_telegram_alert('''$message'''))"
    try {
        & uv run python -c $cmd 2>&1 | Out-Null
    } catch {
        Write-WatchdogLog "Failed to dispatch Telegram alert: $_"
    }
}

function Test-BifrostHealth {
    try {
        $resp = Invoke-WebRequest -Uri "$BifrostUrl/health" -Method Get -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        try {
            $resp2 = Invoke-WebRequest -Uri "$BifrostUrl/" -Method Get -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            return ($resp2.StatusCode -ge 200 -and $resp2.StatusCode -lt 500)
        } catch {
            return $false
        }
    }
}

function Test-FarmHealth {
    if (Test-Path $FarmHeartbeatFile) {
        try {
            $raw = Get-Content -Raw -Path $FarmHeartbeatFile -ErrorAction Stop
            $json = ConvertFrom-Json $raw
            if ($json -and $json.timestamp) {
                $hbTime = [DateTime]::Parse($json.timestamp).ToUniversalTime()
                $now = (Get-Date).ToUniversalTime()
                $ageSeconds = ($now - $hbTime).TotalSeconds
                if ($ageSeconds -lt $UnhealthyThresholdSeconds) {
                    return $true
                }
            }
        } catch {
            return $false
        }
    }
    # Fallback check: is there a farm python process active?
    $procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'farm(\.exe|\s+run|\s+serve)' }
    return ($null -ne $procs -and @($procs).Count -gt 0)
}

function Restart-Bifrost {
    Write-WatchdogLog "Restarting Bifrost..."
    Send-WatchdogAlert "Harness Farm Watchdog: Bifrost gateway unhealthy for >= ${UnhealthyThresholdSeconds}s. Restarting."
    $bifrostScript = Join-Path $root "scripts\start-bifrost.ps1"
    if (Test-Path $bifrostScript) {
        Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$bifrostScript`"" -WindowStyle Hidden
    } else {
        Write-WatchdogLog "Cannot find start-bifrost.ps1 at $bifrostScript"
    }
}

function Restart-Farm {
    Write-WatchdogLog "Restarting Farm..."
    Send-WatchdogAlert "Harness Farm Watchdog: Farm process unhealthy for >= ${UnhealthyThresholdSeconds}s. Restarting."
    # Launch farm run in background
    Start-Process -FilePath "uv" -ArgumentList "run farm run" -WorkingDirectory $root -WindowStyle Hidden
}

Write-WatchdogLog "Starting Harness Farm Watchdog (Interval=${CheckIntervalSeconds}s, Threshold=${UnhealthyThresholdSeconds}s, TestMode=$TestMode)"

$bifrostUnhealthySeconds = 0
$farmUnhealthySeconds = 0

do {
    # 1. Check Bifrost
    $bifrostOk = Test-BifrostHealth
    if ($bifrostOk) {
        if ($bifrostUnhealthySeconds -gt 0) {
            Write-WatchdogLog "Bifrost recovered and is now healthy."
        }
        $bifrostUnhealthySeconds = 0
    } else {
        $bifrostUnhealthySeconds += $CheckIntervalSeconds
        Write-WatchdogLog "Bifrost unhealthy (${bifrostUnhealthySeconds}s / ${UnhealthyThresholdSeconds}s)"
        if ($bifrostUnhealthySeconds -ge $UnhealthyThresholdSeconds) {
            Restart-Bifrost
            $bifrostUnhealthySeconds = 0
        }
    }

    # 2. Check Farm
    $farmOk = Test-FarmHealth
    if ($farmOk) {
        if ($farmUnhealthySeconds -gt 0) {
            Write-WatchdogLog "Farm recovered and is now healthy."
        }
        $farmUnhealthySeconds = 0
    } else {
        $farmUnhealthySeconds += $CheckIntervalSeconds
        Write-WatchdogLog "Farm unhealthy (${farmUnhealthySeconds}s / ${UnhealthyThresholdSeconds}s)"
        if ($farmUnhealthySeconds -ge $UnhealthyThresholdSeconds) {
            Restart-Farm
            $farmUnhealthySeconds = 0
        }
    }

    if ($TestMode) {
        Write-WatchdogLog "[TestMode] Single iteration complete: Bifrost=$bifrostOk, Farm=$farmOk"
        break
    }

    Start-Sleep -Seconds $CheckIntervalSeconds
} while ($true)
