# Windows 작업 스케줄러 — 평일 아침 구간에 Stocke 서버 자동 기동 (꺼져 있을 때만)
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_morning_server_task.ps1
#   powershell ... -WindowStart "08:30" -WindowEnd "08:50" -IntervalMinutes 5
#   powershell ... -Uninstall

param(
    [string]$WindowStart = '08:30',
    [string]$WindowEnd = '08:50',
    [int]$IntervalMinutes = 5,
    [string]$TaskName = 'Stocke-MorningServerWatch',
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$EnsureScript = Join-Path $Root 'scripts\ensure_server_running.ps1'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "작업 제거됨: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $EnsureScript)) {
    Write-Host "ensure_server_running.ps1 을 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

if ($IntervalMinutes -lt 1) {
    Write-Host "IntervalMinutes 는 1 이상이어야 합니다." -ForegroundColor Red
    exit 1
}

try {
    $startAt = [DateTime]::ParseExact($WindowStart, 'HH:mm', $null)
    $endAt = [DateTime]::ParseExact($WindowEnd, 'HH:mm', $null)
} catch {
    Write-Host "시간 형식 오류. 예: -WindowStart `"08:30`" -WindowEnd `"08:50`"" -ForegroundColor Red
    exit 1
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$EnsureScript`" -MorningWindowOnly -WindowStart `"$WindowStart`" -WindowEnd `"$WindowEnd`""

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument $arguments `
    -WorkingDirectory $Root

$weekdays = @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)

$triggers = New-Object System.Collections.Generic.List[object]
$cursor = $startAt
while ($cursor.TimeOfDay -le $endAt.TimeOfDay) {
    $atLabel = $cursor.ToString('HH:mm')
    $triggers.Add((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $atLabel))
    $cursor = $cursor.AddMinutes($IntervalMinutes)
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Stocke 서버 평일 $WindowStart~$WindowEnd ${IntervalMinutes}분마다 기동 확인 (꺼져 있을 때만)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  평일 $WindowStart ~ $WindowEnd, ${IntervalMinutes}분 간격 ($($triggers.Count)회)"
Write-Host "  스크립트: $EnsureScript"
Write-Host "  로그: $Root\logs\scheduler_run.log"
Write-Host ""
Write-Host "확인: 작업 스케줄러 → $TaskName"
Write-Host "제거: powershell -File scripts\install_morning_server_task.ps1 -Uninstall"
Write-Host "즉시 테스트: powershell -File scripts\ensure_server_running.ps1 -MorningWindowOnly"
Write-Host ""
