# Windows 작업 스케줄러 - 장 마감 후 종목 뉴스/키워드 배치 (미니PC 기본)
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_stock_news_batch_task.ps1
#   powershell ... -At "16:30" -Uninstall
#   powershell ... -FullMarket   # 전종목(비권장, 수시간·고CPU)

param(
    [string]$At = "16:30",
    [string]$TaskName = "stocke-stock-news-batch",
    [ValidateSet("theme", "all")]
    [string]$Universe = "theme",
    [int]$MaxStocksPerDay = 120,
    [int]$MaxStocksPerRun = 40,
    [switch]$FullMarket,
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

if ($FullMarket) {
    $Universe = "all"
    if ($MaxStocksPerDay -lt 1000) { $MaxStocksPerDay = 0 }  # 0 = 무제한
    if ($MaxStocksPerRun -lt 100) { $MaxStocksPerRun = 100 }
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

if ($SingleRun) {
    $scriptPath = Join-Path $Root "scripts\stock_news_daily_batch.py"
    if (!(Test-Path $scriptPath)) { throw "batch script not found: $scriptPath" }
    $taskArgs = "`"$scriptPath`" --universe $Universe --max-stocks-per-day $MaxStocksPerDay --max-stocks-per-run $MaxStocksPerRun"
} else {
    $scriptPath = Join-Path $Root "scripts\continue_stock_news_batch.py"
    if (!(Test-Path $scriptPath)) { throw "auto-loop script not found: $scriptPath" }
    $taskArgs = "`"$scriptPath`""
}

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $taskArgs `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Daily -At $atTime

$limitHours = if ($FullMarket) { 6 } else { 2 }
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $limitHours)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$desc = if ($FullMarket) {
    "stocke stock news FULL market (heavy)"
} else {
    "stocke stock news light: theme stocks, max $MaxStocksPerDay/day"
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $desc `
    -Force | Out-Null

Write-Host ""
Write-Host "registered: $TaskName" -ForegroundColor Green
Write-Host "  daily at: $At"
Write-Host "  command: $pythonExe $taskArgs"
Write-Host "  universe: $Universe  max/day: $MaxStocksPerDay  chunk: $MaxStocksPerRun"
Write-Host "  log: $Root\logs\stock_news_daily_batch.log"
if (-not $SingleRun) {
    Write-Host "  mode: auto-continue until day cap (Config: STOCK_NEWS_*)"
}
Write-Host ""
Write-Host "Tip: set STOCK_NEWS_MAX_STOCKS_PER_DAY / STOCK_NEWS_UNIVERSE in .env" -ForegroundColor DarkGray
Write-Host ""
