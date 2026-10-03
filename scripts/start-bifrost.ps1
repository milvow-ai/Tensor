# Start the local Bifrost LLM gateway (127.0.0.1:8080). Config + virtual keys live in D:\dev-cache\bifrost (not in git).
# The OpenRouter key is read from Hermes' default .env into this process only; it is never printed or written elsewhere.
$line = Get-Content "$env:LOCALAPPDATA\hermes\.env" | Where-Object { $_ -match '^\s*OPENROUTER_API_KEY\s*=' } | Select-Object -First 1
if (-not $line) { "OPENROUTER_API_KEY not found in Hermes .env"; exit 2 }
$env:OPENROUTER_API_KEY = (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
$env:npm_config_cache = "D:\dev-cache\npm"
npx -y @maximhq/bifrost -app-dir "D:\dev-cache\bifrost" -port 8080 -host 127.0.0.1 -log-style pretty *> "D:\dev-cache\bifrost\server.log"
