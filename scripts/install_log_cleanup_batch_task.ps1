# Windows 작업 스케줄러 — 매일 로그 7일 보관 정리
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_log_cleanup_batch_task.ps1
#   powershell ... -At "23:00"
#   powershell ... -Uninstall

param(
    [string]$TaskName = "stocke-log-cleanup",
    [string]$At = "23:00",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$batPath = Join-Path $projectRoot "run_log_cleanup_batch.bat"

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
    throw "시간 형식 오류. 예: -At `"23:00`""
}

$action = New-ScheduledTaskAction `
    -Execute $batPath `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

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
    -Description "Stocke 매일 $At 로그 7일 보관 정리 (stock_pipeline.log, server.log, logs/*.log)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  스케줄: 매일 $At"
Write-Host "  launcher: $batPath"
Write-Host "  log: $projectRoot\logs\log_cleanup_batch.log"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_log_cleanup_batch_task.ps1 -Uninstall"
Write-Host "미리보기: python scripts\log_cleanup_batch.py --dry-run"
Write-Host "즉시 실행: $batPath"
Write-Host ""
