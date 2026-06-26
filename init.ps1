param(
    [switch]$CheckOnly,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$ArgsList = @()
if ($CheckOnly) {
    $ArgsList += "--check-only"
}
if ($SkipTests) {
    $ArgsList += "--skip-tests"
}

python scripts/harness/init_check.py @ArgsList
exit $LASTEXITCODE
