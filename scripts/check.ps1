# Zero-token check gate (S). Run from anywhere: powershell -File scripts/check.ps1 [-Expect <glob,...>]
# Exit 0 only if every step passes. Prints one PASS/FAIL line per step plus failing output tails.
param([string[]]$Expect = @())
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:UV_CACHE_DIR = "D:\dev-cache\uv"; $env:UV_PYTHON_INSTALL_DIR = "D:\dev-cache\uv-python"
$failed = 0

function Step([string]$name, [scriptblock]$cmd) {
  $out = & $cmd 2>&1 | Out-String
  if ($LASTEXITCODE -eq 0) { "PASS  $name" } else { "FAIL  $name"; ($out -split "`n" | Select-Object -Last 25) -join "`n"; $script:failed++ }
}

Step "ruff"   { uv run ruff check farm tests }
Step "mypy"   { uv run mypy }
Step "pytest" { uv run pytest -q -p no:cacheprovider }

# Secret grep over tracked + untracked (non-ignored) files: key-shaped strings must never be committed.
$files = git ls-files --cached --others --exclude-standard | Where-Object { $_ -notmatch '^(library|research/data)/' -and $_ -notmatch '\.lock$' }
$pat = 'sk-or-v1-[0-9a-f]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|eyJhbGciOi[A-Za-z0-9_-]{30,}|sk-bf-(?!farm-p0-local-test)[A-Za-z0-9-]{16,}|postgres(ql)?://[^:\s]+:[^@\s]{6,}@'
# -LiteralPath: Next.js route folders like [...slug] are wildcard patterns to PowerShell.
$hits = $files | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | ForEach-Object { Get-Item -LiteralPath $_ } |
  Select-String -Pattern $pat -List | ForEach-Object { "$($_.Path):$($_.LineNumber)" }
"      secret-grep scanned $(@($files).Count) files"
if ($hits) { "FAIL  secret-grep"; $hits; $failed++ } else { "PASS  secret-grep" }

# Optional: files the brief said would change must show up in git status.
$Expect = @($Expect | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Expect.Count -gt 0) {
  $changed = git status --porcelain --untracked-files=all | ForEach-Object { $_.Substring(3) }
  $missing = $Expect | Where-Object { $e = $_; -not ($changed | Where-Object { $_ -like $e }) }
  if ($missing) { "FAIL  expected-files: missing $($missing -join ', ')"; $failed++ } else { "PASS  expected-files" }
}

if ($failed -gt 0) { "RESULT: $failed step(s) failed"; exit 1 } else { "RESULT: all passed"; exit 0 }
