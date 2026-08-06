param(
    [string]$Source,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

$arguments = @((Join-Path $PSScriptRoot "export_database.py"))
if ($Source) {
    $arguments += @("--source", $Source)
}
if ($OutputDirectory) {
    $arguments += @("--output-dir", $OutputDirectory)
}

& $python @arguments
exit $LASTEXITCODE
