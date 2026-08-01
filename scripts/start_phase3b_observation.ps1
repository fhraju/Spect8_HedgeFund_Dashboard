param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$environmentPath = Join-Path $repositoryRoot "backend\.env"

if (-not (Test-Path -LiteralPath $environmentPath)) {
    throw "Create ignored backend/.env before starting observation."
}

foreach ($line in Get-Content -LiteralPath $environmentPath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
        continue
    }
    $name = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

$env:SPECT8_MARKET_DATA_PROVIDER = "twelve_data"
$env:SPECT8_MARKET_DATA_RUNTIME_ENABLED = "true"
$env:SPECT8_AUTO_SEED_SYNTHETIC = "false"

try {
    Set-Location $repositoryRoot
    & ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app `
        --host 127.0.0.1 --port $Port
}
finally {
    Remove-Item Env:\TWELVE_DATA_API_KEY -ErrorAction SilentlyContinue
}
