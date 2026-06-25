param(
    [string]$TaskName = "CustomerServiceDailyAnalytics",
    [string]$Time = "00:10",
    [string]$PythonPath = "",
    [string]$ProjectDir = ""
)

if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = Resolve-Path (Join-Path $PSScriptRoot "..")
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonPath = $VenvPython
    } else {
        $PythonPath = "python"
    }
}

$ScriptPath = Join-Path $ProjectDir "scripts\generate_daily_usage_report.py"
$ReportsDir = Join-Path $ProjectDir "reports\daily"
$ActionArgs = "`"$ScriptPath`" --date yesterday --output-dir `"$ReportsDir`""
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument $ActionArgs -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Generate daily customer-service analytics report" -Force | Out-Null
Write-Output "Installed scheduled task '$TaskName' at $Time."
Write-Output "Command: $PythonPath $ActionArgs"
