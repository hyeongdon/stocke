# Windows 작업 스케줄러 — 차트 OHLCV 마트 배치
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_chart_ohlcv_batch_task.ps1
#   powershell ... -DailyTime "20:30" -SaturdayTime "22:00"
#   powershell ... -Uninstall
#
# 평일 장후(기본 20:30): 일봉만
# 토요일(기본 22:00): 주봉 → 월봉 순차
# (일봉 cron UTC 11:30 = KST 20:30, 주·월봉 cron UTC 13:00 = KST 22:00)

param(
    [string]$DailyTime = "20:30",
    [string]$SaturdayTime = "22:00",
    [string]$DailyTaskName = "Stocke-ChartOhlcvBatch",
    [string]$WeeklyMonthlyTaskName = "Stocke-ChartOhlcvWeeklyMonthly",
    [switch]$Uninstall
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DailyBat = Join-Path $Root 'run_chart_ohlcv_daily_batch.bat'
$WeeklyMonthlyBat = Join-Path $Root 'run_chart_ohlcv_weekly_monthly_batch.bat'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $DailyTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $WeeklyMonthlyTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "작업 제거됨: $DailyTaskName, $WeeklyMonthlyTaskName" -ForegroundColor Green
    exit 0
}

foreach ($bat in @($DailyBat, $WeeklyMonthlyBat)) {
    if (-not (Test-Path $bat)) {
        Write-Host "배치 파일을 찾을 수 없습니다: $bat" -ForegroundColor Red
        exit 1
    }
}

try {
    $dailyAt = [DateTime]::ParseExact($DailyTime, 'HH:mm', $null)
    $satAt = [DateTime]::ParseExact($SaturdayTime, 'HH:mm', $null)
} catch {
    Write-Host "시간 형식 오류. 예: -DailyTime `"20:30`" -SaturdayTime `"20:30`"" -ForegroundColor Red
    exit 1
}

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

# 평일 일봉
$dailyAction = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$DailyBat`"" `
    -WorkingDirectory $Root
$dailyTrigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $dailyAt

Register-ScheduledTask `
    -TaskName $DailyTaskName `
    -Action $dailyAction `
    -Trigger $dailyTrigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Stocke 차트 OHLCV 마트 일봉 (fundamental) 평일 장후 $DailyTime 실행" `
    -Force | Out-Null

# 토요일 주봉 → 월봉 순차
$wmAction = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$WeeklyMonthlyBat`"" `
    -WorkingDirectory $Root
$wmTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At $satAt

Register-ScheduledTask `
    -TaskName $WeeklyMonthlyTaskName `
    -Action $wmAction `
    -Trigger $wmTrigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Stocke 차트 OHLCV 마트 주봉→월봉 순차 (fundamental) 토요일 $SaturdayTime 실행" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료" -ForegroundColor Green
Write-Host "  $DailyTaskName : 평일 $DailyTime 일봉"
Write-Host "  $WeeklyMonthlyTaskName : 토요일 $SaturdayTime 주봉→월봉 순차"
Write-Host "  로그: $Root\logs\chart_ohlcv_mart_batch.log"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_chart_ohlcv_batch_task.ps1 -Uninstall"
Write-Host "즉시 테스트(일봉): $DailyBat"
Write-Host "즉시 테스트(주/월): $WeeklyMonthlyBat"
Write-Host ""
