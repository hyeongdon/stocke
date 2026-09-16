# Windows 작업 스케줄러 — 평일 장 종료 후 실시간 3분봉 구독 정리
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_realtime_candle_cleanup_task.ps1
#   powershell ... -At "20:05"                          # 실행 시각 변경
#   powershell ... -Host "http://my-server:8000"        # 원격 서버
#   powershell ... -Uninstall                           # 작업 제거

param(
    [string]$TaskName = "stocke-realtime-candle-cleanup",
    [string]$At       = "20:00",
    [string]$Host     = "http://localhost:8000",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$batPath     = Join-Path $projectRoot "run_realtime_candle_cleanup.bat"

# ── 제거 ─────────────────────────────────────────────────────────────
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "작업 제거 완료: $TaskName" -ForegroundColor Green
    exit 0
}

# ── 유효성 검사 ───────────────────────────────────────────────────────
if (!(Test-Path $batPath)) {
    throw "배치 런처를 찾을 수 없습니다: $batPath"
}
try {
    $null = [DateTime]::ParseExact($At, "HH:mm", $null)
} catch {
    throw "시간 형식 오류. 예: -At `"20:05`""
}

$weekdays = @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
)

# ── 작업 등록 ─────────────────────────────────────────────────────────
$action = New-ScheduledTaskAction `
    -Execute $batPath `
    -Argument "--host `"$Host`"" `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $At

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

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
    -Description "Stocke 평일 $At 실시간 3분봉 구독 해제 + 봉 히스토리 초기화" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  스케줄: 평일 $At"
Write-Host "  대상 서버: $Host"
Write-Host "  launcher: $batPath"
Write-Host "  log: $projectRoot\logs\realtime_candle_cleanup_batch.log"
Write-Host ""
Write-Host "제거:  powershell -File scripts\install_realtime_candle_cleanup_task.ps1 -Uninstall"
Write-Host "테스트: $batPath"
Write-Host "다른 시각으로 재등록 예시:"
Write-Host "  powershell -File scripts\install_realtime_candle_cleanup_task.ps1 -At `"20:30`""
Write-Host ""
