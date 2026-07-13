# Windows 작업 스케줄러 - 장 마감 후 전체 종목 뉴스/키워드 배치
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_stock_news_batch_task.ps1
#       powershell ... -At "16:30" -Uninstall

param(
    [string]$At = "16:30",
    [string]$TaskName = "stocke-stock-news-batch",
    [int]$MaxStocksPerRun = 100,
    [switch]$SingleRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$pythonExe = Join-Path $Root "venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) {
    $pythonExe = Join-Path $Root ".venv\Scripts\python.exe"
}
if (!(Test-Path $pythonExe)) {
    $pythonExe = "python"
}

if ($SingleRun) {
    $scriptPath = Join-Path $Root "scripts\stock_news_daily_batch.py"
    if (!(Test-Path $scriptPath)) {
        throw "batch script not found: $scriptPath"
    }
    $taskArgs = "`"$scriptPath`" --max-stocks-per-run $MaxStocksPerRun"
} else {
    $scriptPath = Join-Path $Root "scripts\continue_stock_news_batch.py"
    if (!(Test-Path $scriptPath)) {
        throw "auto-loop script not found: $scriptPath"
    }
    $taskArgs = "`"$scriptPath`""
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed: $TaskName" -ForegroundColor Green
    exit 0
}

try {
    $atTime = [DateTime]::ParseExact($At, "HH:mm", $null)
} catch {
    throw "invalid time format, use -At 16:30"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $taskArgs `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Daily -At $atTime

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "stocke stock news keyword batch after market close" `
    -Force | Out-Null

Write-Host ""
Write-Host "registered: $TaskName" -ForegroundColor Green
Write-Host "  daily at: $At"
Write-Host "  command: $pythonExe $taskArgs"
Write-Host "  log: $Root\logs\stock_news_daily_batch.log"
if (-not $SingleRun) {
    Write-Host "  mode: auto-continue (100개 청크 반복, 전체 완료까지)"
}
Write-Host ""
