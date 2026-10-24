# Run one Hermes brief headless, pinned to a working directory, with usage accounting.
# Usage: powershell -File scripts/run-hermes.ps1 -Brief briefs/X.md [-Cwd <dir>] [-Model <model>] [-Profile farm-builder|default] [-Provider custom:bedrock-mantle]
# Two routes: Bifrost profiles (farm-builder, capped virtual key at 127.0.0.1:8080), or the default profile with
# -Provider custom:bedrock-mantle for Bedrock models such as zai.glm-5 (verified 2026-10-24).
# TERMINAL_CWD must be set: Hermes' --in flag does not move tool cwd, and without it files land in $HOME.
param(
  [Parameter(Mandatory = $true)][string]$Brief,
  [string]$Cwd = (Split-Path -Parent $PSScriptRoot),
  [string]$Model = "openrouter/deepseek/deepseek-v4-flash",
  [string]$Profile = "farm-builder",
  [string]$Provider = ""
)
$ErrorActionPreference = "Stop"
$Cwd = (Resolve-Path $Cwd).Path
$briefPath = if ([IO.Path]::IsPathRooted($Brief)) { $Brief } else { Join-Path $Cwd $Brief }
$id = [IO.Path]::GetFileNameWithoutExtension($briefPath)
$outDir = "D:\dev-cache\runs\$id"; New-Item -ItemType Directory -Force $outDir | Out-Null
if (-not $Provider) {
  try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8080/health" -TimeoutSec 3 | Out-Null } catch { "Bifrost is not running: start scripts/start-bifrost.ps1 first"; exit 3 }
}

$prompt = "You are a builder. Execute the brief in $briefPath exactly, following its rules. Do not touch any other file. Finish with the reply format the brief asks for."
$env:TERMINAL_CWD = $Cwd
$env:UV_CACHE_DIR = "D:\dev-cache\uv"; $env:UV_PYTHON_INSTALL_DIR = "D:\dev-cache\uv-python"; $env:npm_config_cache = "D:\dev-cache\npm"
$sw = [Diagnostics.Stopwatch]::StartNew()
$hermesArgs = @()
if ($Profile -and $Profile -ne "default") { $hermesArgs += @("-p", $Profile) }
$hermesArgs += @("-z", "`"$prompt`"")
if ($Provider) { $hermesArgs += @("--provider", $Provider) }
$hermesArgs += @("-m", $Model, "--usage-file", "$outDir\usage.json")
$p = Start-Process -FilePath hermes -WorkingDirectory $Cwd -NoNewWindow -PassThru -Wait `
  -RedirectStandardOutput "$outDir\out.txt" -RedirectStandardError "$outDir\err.txt" `
  -ArgumentList $hermesArgs
"hermes exit=$($p.ExitCode) secs=$([int]$sw.Elapsed.TotalSeconds) out=$outDir"
if (Test-Path "$outDir\usage.json") {
  $u = Get-Content "$outDir\usage.json" -Raw | ConvertFrom-Json
  "cost_usd=$([math]::Round($u.total_including_auxiliary.estimated_cost_usd, 5)) tokens=$($u.total_tokens) calls=$($u.api_calls) failed=$($u.failed)"
  Add-Content "D:\dev-cache\runs\ledger.tsv" "$(Get-Date -Format s)`thermes`t$id`t$Model`t$($u.total_including_auxiliary.estimated_cost_usd)`t$($u.total_tokens)"
}
Get-Content "$outDir\out.txt" -Tail 14
