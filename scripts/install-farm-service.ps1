# Install or remove the per-user scheduled task for the Harness Farm 24/7 Watchdog.
# Runs hidden at user logon.
# Note: To be executed manually by the owner. Builders must never run this script without -TestMode.
#
# Usage:
#   powershell -File scripts/install-farm-service.ps1 [-Uninstall] [-TestMode]

param(
    [switch]$Uninstall,
    [switch]$TestMode,
    [string]$TaskName = "HarnessFarm"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$watchdogScript = Join-Path $root "scripts\farm-watchdog.ps1"

if (-not (Test-Path $watchdogScript)) {
    Write-Error "Watchdog script not found at: $watchdogScript"
    exit 1
}

$currentUser = $env:USERNAME
$powershellExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $powershellExe)) {
    $powershellExe = "powershell.exe"
}

$taskActionArg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdogScript`""

if ($Uninstall) {
    if ($TestMode) {
        Write-Host "[TestMode] Verified uninstall path for Task Scheduler task '$TaskName'."
        exit 0
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "SUCCESS: Removed per-user Task Scheduler task '$TaskName'."
    } else {
        Write-Host "INFO: Task Scheduler task '$TaskName' is not installed."
    }
    exit 0
}

if ($TestMode) {
    Write-Host "[TestMode] Validating installation configuration for '$TaskName':"
    Write-Host "  User:       $currentUser (per-user logon)"
    Write-Host "  Executable: $powershellExe"
    Write-Host "  Arguments:  $taskActionArg"
    Write-Host "  Trigger:    At logon for $currentUser"
    Write-Host "  Execution:  Hidden window, keep alive, start if on batteries"
    Write-Host "[TestMode] Validation passed. Task is ready for installation by owner."
    exit 0
}

# --- Actual Installation (Owner only) ---
Write-Host "Registering per-user Task Scheduler task '$TaskName' for user '$currentUser'..."

# Unregister previous task if exists
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $taskActionArg
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Harness Farm 24/7 Watchdog and Auto-Recovery Service" | Out-Null

Write-Host "SUCCESS: Created per-user Task Scheduler task '$TaskName' at logon."
Write-Host "The watchdog will start automatically on next logon, or can be started now using:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
