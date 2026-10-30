# Windows 작업 스케줄러 — 부팅/로그온 + 평일 아침 구간에 가장 높은 권한으로 proxy.bat ensure 실행
#   ensure: Caddy 가 이미 80 포트에서 돌고 있으면 스킵, 꺼져 있으면 백엔드(8000) 대기 후 시작
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_proxy_startup_task.ps1
#   powershell ... -WindowStart "08:30" -WindowEnd "09:00" -IntervalMinutes 5 -WaitBackendMinutes 180
#   powershell ... -Uninstall

param(
    [string]$TaskName = 'Stocke-ProxyStartup',
    [int]$WaitBackendMinutes = 180,
    [string]$WindowStart = '08:30',
    [string]$WindowEnd = '09:00',
    [int]$IntervalMinutes = 5,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ProxyBat = Join-Path $Root 'proxy.bat'
$LogFile = Join-Path $Root 'logs\proxy_task.log'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "관리자 권한이 필요합니다. UAC 창에서 [예]를 누르세요." -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$($MyInvocation.MyCommand.Path)`"",
        '-TaskName', $TaskName, '-WaitBackendMinutes', $WaitBackendMinutes,
        '-WindowStart', $WindowStart, '-WindowEnd', $WindowEnd, '-IntervalMinutes', $IntervalMinutes)
    if ($Uninstall) { $argList += '-Uninstall' }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "작업 제거됨: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $ProxyBat)) {
    Write-Host "proxy.bat 을 찾을 수 없습니다: $ProxyBat" -ForegroundColor Red
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
    Write-Host "시간 형식 오류. 예: -WindowStart `"08:30`" -WindowEnd `"09:00`"" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null

$waitSec = [Math]::Max(0, $WaitBackendMinutes) * 60
$cmdLine = "/c `"set STOCKE_PROXY_NONINTERACTIVE=1&& set STOCKE_PROXY_WAIT_BACKEND_SEC=$waitSec&& `"$ProxyBat`" ensure >> `"$LogFile`" 2>&1`""

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument $cmdLine `
    -WorkingDirectory $Root

$weekdays = @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)

$triggers = New-Object System.Collections.Generic.List[object]
$triggers.Add((New-ScheduledTaskTrigger -AtStartup))
$triggers.Add((New-ScheduledTaskTrigger -AtLogOn))
$morningCount = 0
$cursor = $startAt
while ($cursor.TimeOfDay -le $endAt.TimeOfDay) {
    $triggers.Add((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $cursor.ToString('HH:mm')))
    $morningCount++
    $cursor = $cursor.AddMinutes($IntervalMinutes)
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ($WaitBackendMinutes + 15))

$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Stocke Caddy 프록시(80/443 -> 8000) 부팅/로그온 + 평일 $WindowStart~$WindowEnd ${IntervalMinutes}분마다 확인 (꺼져 있을 때만 시작, 백엔드 최대 ${WaitBackendMinutes}분 대기)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  트리거: 부팅 시 + 로그온 시 + 평일 $WindowStart ~ $WindowEnd ${IntervalMinutes}분 간격 (${morningCount}회)"
Write-Host "  실행: $ProxyBat ensure (SYSTEM, 가장 높은 권한)"
Write-Host "  로그: $LogFile"

Start-ScheduledTask -TaskName $TaskName
Write-Host "  지금 1회 실행함 (Caddy 꺼져 있으면 시작)"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_proxy_startup_task.ps1 -Uninstall"
Write-Host ""
