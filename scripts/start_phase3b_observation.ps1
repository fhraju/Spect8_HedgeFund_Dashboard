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

# The protected Next.js server and backend must share the same internal key.
# Keep the value in the already-ignored frontend environment file; if the
# backend file does not override it, import only this one setting without
# copying or printing the secret.
if (-not $env:SPECT8_INTERNAL_API_KEY) {
    $frontendEnvironmentPath = Join-Path $repositoryRoot "frontend\.env.local"
    if (-not (Test-Path -LiteralPath $frontendEnvironmentPath)) {
        throw "frontend/.env.local is required for the protected dashboard."
    }
    foreach ($line in Get-Content -LiteralPath $frontendEnvironmentPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $separator).Trim()
        if ($name -eq "SPECT8_INTERNAL_API_KEY") {
            $value = $trimmed.Substring($separator + 1).Trim()
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
            break
        }
    }
    if (-not $env:SPECT8_INTERNAL_API_KEY) {
        throw "SPECT8_INTERNAL_API_KEY is not configured."
    }
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
