# Windows 작업 스케줄러 — 부팅/로그온 시 가장 높은 권한으로 proxy.bat start 실행
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_proxy_startup_task.ps1
#   powershell ... -WaitBackendMinutes 240
#   powershell ... -Uninstall

param(
    [string]$TaskName = 'Stocke-ProxyStartup',
    [int]$WaitBackendMinutes = 180,
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
        '-TaskName', $TaskName, '-WaitBackendMinutes', $WaitBackendMinutes)
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

New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null

$waitSec = [Math]::Max(0, $WaitBackendMinutes) * 60
$cmdLine = "/c `"set STOCKE_PROXY_NONINTERACTIVE=1&& set STOCKE_PROXY_WAIT_BACKEND_SEC=$waitSec&& `"$ProxyBat`" start >> `"$LogFile`" 2>&1`""

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument $cmdLine `
    -WorkingDirectory $Root

$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)

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
    -Description "Stocke Caddy 프록시(80 -> 8000) 부팅/로그온 시 자동 시작 (백엔드 최대 ${WaitBackendMinutes}분 대기)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  트리거: 부팅 시 + 로그온 시 (SYSTEM, 가장 높은 권한)"
Write-Host "  실행: $ProxyBat start"
Write-Host "  로그: $LogFile"
Write-Host ""
Write-Host "즉시 테스트: schtasks /Run /TN $TaskName"
Write-Host "제거: powershell -File scripts\install_proxy_startup_task.ps1 -Uninstall"
Write-Host ""
