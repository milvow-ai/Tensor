# Harness Farm round runner: one command per step so the lead spends almost no tokens.
#   start  : create/reuse worktree D:\Harness Farm\wt-<name> from the main branch; run the builder DETACHED (survives the
#            Claude session ending), then the check gate there; writes D:\dev-cache\runs\<name>\status.json
#   status : one line per round; with -Name also the builder reply tail, gate tail and changed files
#   merge  : commit the worktree, merge main into it, gate (Python + Console when console/ changed), merge --no-ff into
#            main, push
# Usage:
#   powershell -File scripts/round.ps1 -Action start  -Brief briefs/M2a-resilience-ranking.md [-Name m2a] [-Builder agy|hermes] [-Model m]
#   powershell -File scripts/round.ps1 -Action status [-Name m2a]
#   powershell -File scripts/round.ps1 -Action merge  -Name m2a -Message "M2a: resilience and ranking"
param(
  [Parameter(Mandatory = $true)][ValidateSet('start', 'status', 'merge', 'work')][string]$Action,
  [string]$Brief,
  [string]$Name,
  [ValidateSet('agy', 'hermes')][string]$Builder = 'agy',
  [string]$Model = '',
  [string]$Message = ''
)
$ErrorActionPreference = 'Continue'
$Main = 'claude/awesome-archimedes-vb1t7y'
$Repo = 'D:\Harness Farm\Tensor'
$Runs = 'D:\dev-cache\runs'
$Trailer = "`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
if (-not $Name -and $Brief) { $Name = ([IO.Path]::GetFileNameWithoutExtension($Brief)).ToLower() }
$Wt = "D:\Harness Farm\wt-$Name"
$RunDir = Join-Path $Runs $Name
$env:UV_CACHE_DIR = 'D:\dev-cache\uv'; $env:FARM_DATA_DIR = 'D:/farm-data'; $env:npm_config_cache = 'D:\dev-cache\npm'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev-cache\ms-playwright'

function Write-Json($path, $obj) { [IO.File]::WriteAllText($path, ($obj | ConvertTo-Json -Depth 6)) }

function Invoke-Gate($dir) {
  $out = powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'scripts\check.ps1') 2>&1 | Out-String
  $ok = $out -match 'RESULT: all passed'
  $tail = @($out -split "`r?`n" | Where-Object { $_ -match 'PASS|FAIL|RESULT|failed|passed|Error' } | Select-Object -Last 10)
  $consoleTouched = @(git -C $dir status --short -- console).Count + @(git -C $dir diff --name-only "$Main...HEAD" -- console).Count
  if ($consoleTouched -gt 0) {
    Push-Location (Join-Path $dir 'console')
    pnpm install --frozen-lockfile 2>&1 | Out-Null
    $null = pnpm check 2>&1 | Out-String; $cok = ($LASTEXITCODE -eq 0)
    $null = pnpm test 2>&1 | Out-String; $vok = ($LASTEXITCODE -eq 0)
    $e2e = pnpm test:e2e 2>&1 | Out-String; $eok = ($LASTEXITCODE -eq 0)
    Pop-Location
    $ok = $ok -and $cok -and $vok -and $eok
    $tail += "console: check=$cok vitest=$vok e2e=$eok " + ((@($e2e -split "`r?`n" | Where-Object { $_ -match '\d+ (passed|failed)' }) | Select-Object -Last 2) -join ' ')
  }
  return @{ ok = [bool]$ok; tail = ($tail -join "`n") }
}

if ($Action -eq 'start') {
  if (-not $Brief) { 'start needs -Brief'; exit 2 }
  New-Item -ItemType Directory -Force $RunDir | Out-Null
  if (-not (Test-Path $Wt)) {
    if (git -C $Repo branch --list $Name) { git -C $Repo worktree add -q $Wt $Name 2>&1 | Out-Null }
    else { git -C $Repo worktree add -q -b $Name $Wt $Main 2>&1 | Out-Null }
    if (Test-Path "$Repo\console\.env.local") { Copy-Item "$Repo\console\.env.local" "$Wt\console\.env.local" }
  }
  elseif (@(git -C $Wt status --short).Count -gt 0) {
    $bp = Join-Path $Wt $Brief
    if (-not (Select-String -Path $bp -Pattern '## Resume note' -Quiet)) {
      Add-Content $bp "`n## Resume note`nA previous run stopped early. The files listed by ``git status`` are its partial work: review them, keep what is right, fix what is not, finish the brief.`n"
    }
  }
  Write-Json "$RunDir\status.json" @{ name = $Name; brief = $Brief; builder = $Builder; state = 'running'; started = (Get-Date -Format s) }
  $argList = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\scripts\round.ps1`" -Action work -Name $Name -Brief `"$Brief`" -Builder $Builder"
  if ($Model) { $argList += " -Model $Model" }
  Start-Process powershell -ArgumentList $argList -WindowStyle Hidden | Out-Null
  "started $Name ($Builder) in $Wt  ->  check: scripts/round.ps1 -Action status -Name $Name"
  exit 0
}

if ($Action -eq 'work') {
  $t0 = Get-Date
  if ($Builder -eq 'agy') {
    $m = $(if ($Model) { $Model } else { 'gemini-3.8-flash-high' })
    $b = powershell -NoProfile -ExecutionPolicy Bypass -File "$Wt\scripts\run-agy.ps1" -Brief $Brief -Model $m 2>&1 | Out-String
  }
  else {
    $m = $(if ($Model) { $Model } else { 'qwen.qwen3-coder-next' })
    $prof = $(if ($m -like 'qwen.*') { 'farm-builder-bedrock' } else { 'farm-builder' })
    $b = powershell -NoProfile -ExecutionPolicy Bypass -File "$Repo\scripts\run-hermes.ps1" -Brief $Brief -Cwd $Wt -Model $m -Profile $prof 2>&1 | Out-String
  }
  $g = Invoke-Gate $Wt
  $files = @(git -C $Wt status --short)
  Write-Json "$RunDir\status.json" @{
    name = $Name; brief = $Brief; builder = $Builder; model = $m; state = 'done'
    started = $t0.ToString('s'); finished = (Get-Date -Format s); minutes = [int]((Get-Date) - $t0).TotalMinutes
    builder_tail = ((@($b -split "`r?`n") | Select-Object -Last 18) -join "`n")
    gate_ok = $g.ok; gate_tail = $g.tail; files_changed = $files.Count; files = @($files | Select-Object -First 40)
  }
  exit 0
}

if ($Action -eq 'status') {
  $dirs = @(Get-ChildItem $Runs -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'status.json') })
  if ($Name) { $dirs = @($dirs | Where-Object { $_.Name -eq $Name }) }
  foreach ($d in $dirs) {
    $s = Get-Content (Join-Path $d.FullName 'status.json') -Raw | ConvertFrom-Json
    $gate = $(if ($s.state -ne 'done') { '-' } elseif ($s.gate_ok) { 'PASS' } else { 'FAIL' })
    "{0,-30} {1,-8} {2,-6} gate={3,-4} files={4,-3} started={5} min={6}" -f $s.name, $s.state, $s.builder, $gate, $s.files_changed, $s.started, $s.minutes
    if ($Name) { '--- builder tail'; $s.builder_tail; '--- gate'; $s.gate_tail; '--- files'; ($s.files -join "`n") }
  }
  '--- worktrees'; git -C $Repo worktree list | Select-String 'wt-' | ForEach-Object { $_.Line }
  exit 0
}

if ($Action -eq 'merge') {
  if (-not $Name -or -not (Test-Path $Wt)) { "no worktree for '$Name'"; exit 2 }
  if (-not $Message) { $Message = $Name }
  if (@(git -C $Repo status --porcelain).Count -gt 0) { 'main tree has uncommitted changes: commit them first'; exit 6 }
  git -C $Wt add -A | Out-Null
  if (@(git -C $Wt diff --cached --name-only).Count -gt 0) { git -C $Wt commit -q -m "$Message$Trailer" | Out-Null }
  $null = git -C $Wt merge --no-edit $Main 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { "MERGE CONFLICT (main -> $Name):"; git -C $Wt diff --name-only --diff-filter=U; exit 3 }
  Push-Location $Wt; uv sync -q 2>&1 | Out-Null; Pop-Location
  $g = Invoke-Gate $Wt
  if (-not $g.ok) { "GATE FAIL in ${Name}:"; $g.tail; exit 4 }
  $mm = git -C $Repo merge --no-ff -q $Name -m "Merge ${Name}: $Message$Trailer" 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { 'MERGE INTO MAIN FAILED:'; $mm; exit 5 }
  git -C $Repo push -q origin $Main 2>&1 | Out-Null
  "MERGED $Name into main @ $(git -C $Repo rev-parse --short HEAD), pushed. Gate:"; $g.tail
  exit 0
}
