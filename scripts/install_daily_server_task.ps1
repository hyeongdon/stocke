# Windows 작업 스케줄러 — 매일 지정 시각에 Stocke 서버 자동 기동
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_daily_server_task.ps1
#       powershell ... -Time "08:00" -Uninstall

param(
    [string]$Time = "08:00",
    [string]$TaskName = "Stocke-DailyServerStart",
    [switch]$Uninstall
)

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

try {
    $at = [DateTime]::ParseExact($Time, 'HH:mm', $null)
} catch {
    Write-Host "시간 형식 오류. 예: -Time `"08:00`"" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$EnsureScript`"" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Daily -At $at

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# 로그인한 사용자 세션에서 실행 (미니PC 상시 로그인 가정)
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
    -Description "Stocke 자동매매 서버 매일 $Time 자동 기동 (꺼져 있을 때만)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  시각: 매일 $Time"
Write-Host "  스크립트: $EnsureScript"
Write-Host "  로그: $Root\logs\scheduler_run.log"
Write-Host ""
Write-Host "확인: 작업 스케줄러 → $TaskName"
Write-Host "제거: powershell -File scripts\install_daily_server_task.ps1 -Uninstall"
Write-Host "즉시 테스트: powershell -File scripts\ensure_server_running.ps1"
Write-Host ""
