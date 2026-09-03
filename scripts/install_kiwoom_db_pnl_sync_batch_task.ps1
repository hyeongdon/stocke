# Windows 작업 스케줄러 — 평일 NXT 마감 후 키움↔DB 손익 동기화
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_kiwoom_db_pnl_sync_batch_task.ps1
#   powershell ... -At "19:50"
#   powershell ... -Uninstall

param(
    [string]$TaskName = "stocke-kiwoom-db-pnl-sync",
    [string]$At = "19:50",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$batPath = Join-Path $projectRoot "run_kiwoom_db_pnl_sync_batch.bat"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "작업 제거 완료: $TaskName" -ForegroundColor Green
    exit 0
}

if (!(Test-Path $batPath)) {
    throw "배치 런처를 찾을 수 없습니다: $batPath"
}

try {
    $null = [DateTime]::ParseExact($At, "HH:mm", $null)
} catch {
    throw "시간 형식 오류. 예: -At `"19:50`""
}

$weekdays = @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)

$action = New-ScheduledTaskAction `
    -Execute $batPath `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $At

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

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
    -Description "Stocke 평일 NXT 마감 후(기본 19:50) 키움 실현손익·수수료·잔고 → DB 동기화 (매매 일지보다 먼저)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  스케줄: 평일 $At"
Write-Host "  launcher: $batPath"
Write-Host "  log: $projectRoot\logs\kiwoom_db_pnl_sync_batch.log"
Write-Host "  알림: TELEGRAM_* 설정 시 차이·반영 결과 전송"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_kiwoom_db_pnl_sync_batch_task.ps1 -Uninstall"
Write-Host "즉시 테스트: $batPath"
Write-Host "미리보기: python scripts\kiwoom_db_pnl_sync_batch.py --dry-run --days 90 --no-reconcile"
Write-Host ""
