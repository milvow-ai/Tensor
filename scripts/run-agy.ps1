# Run one brief through Gemini 3.8 Flash High (Antigravity CLI, headless) from the repo root.
# Usage: powershell -File scripts/run-agy.ps1 -Brief briefs/X.md [-Model gemini-3.8-flash-high]
# Output JSON + a usage row in D:\dev-cache\runs\ledger.tsv. Gemini never sees .env values (brief forbids reading .env).
param([Parameter(Mandatory = $true)][string]$Brief, [string]$Model = "gemini-3.8-flash-high")
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:UV_CACHE_DIR = "D:\dev-cache\uv"; $env:UV_PYTHON_INSTALL_DIR = "D:\dev-cache\uv-python"
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\dev-cache\ms-playwright"; $env:npm_config_cache = "D:\dev-cache\npm"
$id = [IO.Path]::GetFileNameWithoutExtension($Brief)
$outDir = "D:\dev-cache\runs\$id"; New-Item -ItemType Directory -Force $outDir | Out-Null
$out = Join-Path $outDir "agy.json"
$prompt = "You are a builder. Execute the brief in $Brief exactly, following its rules. Do not touch any other file. Run every command in the foreground and wait for it to finish; never start a command in the background and poll it (each poll re-sends your whole context). Finish with the reply format the brief asks for."
$sw = [Diagnostics.Stopwatch]::StartNew()
$raw = agy -p $prompt --model $Model --dangerously-skip-permissions --output-format json --print-timeout 3600s 2>&1 | Out-String
[IO.File]::WriteAllText($out, $raw)
"agy exit=$LASTEXITCODE secs=$([int]$sw.Elapsed.TotalSeconds) out=$out"
$i = $raw.IndexOf('{"conversation')
if ($i -lt 0) { "no agy JSON found"; $raw.Substring([Math]::Max(0, $raw.Length - 800)); exit 2 }
$e = $raw.LastIndexOf('}')  # agy may print notices (e.g. 'terminating 1 background task(s)') after the JSON
$j = $raw.Substring($i, $e - $i + 1) | ConvertFrom-Json
"status=$($j.status) tokens=$($j.usage.total_tokens)"
Add-Content "D:\dev-cache\runs\ledger.tsv" "$(Get-Date -Format s)`tagy`t$id`t$Model`t0`t$($j.usage.total_tokens)"
$j.response -split "`n" | Select-Object -Last 14
