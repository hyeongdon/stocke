# 새벽 2시 1회: 키움 테마 수집 + PC 종료 예약
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_kiwoom_theme_once_shutdown_task.ps1
#       powershell ... -At "2026-08-02 02:00"
#       powershell ... -Uninstall

param(
  [string]$TaskName = "stocke-kiwoom-theme-once-shutdown",
  [string]$At = "2026-08-02 02:00",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runner = Join-Path $projectRoot "scripts\run_kiwoom_theme_once_then_shutdown.ps1"
if (!(Test-Path $runner)) {
  throw "러너 스크립트 없음: $runner"
}

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
  Write-Host "작업 제거 완료: $TaskName"
  exit 0
}

try {
  $atTime = [DateTime]::Parse($At)
} catch {
  throw "시간 형식 오류. 예: -At `"2026-08-02 02:00`""
}

if ($atTime -le (Get-Date)) {
  throw "예약 시각이 이미 지났습니다: $atTime"
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument $arg `
  -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -Once -At $atTime

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)

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
  -Description "stocke 키움 테마 1회 수집 후 PC 종료 (점검 후 새벽)" `
  -Force | Out-Null

Write-Host "1회 작업 등록 완료: $TaskName"
Write-Host "  when: $atTime"
Write-Host "  run:  kiwoom-only theme batch + telegram, then shutdown (~90s delay)"
Write-Host "  log:  $projectRoot\logs\theme_mart_batch.log"
Write-Host "제거: powershell -ExecutionPolicy Bypass -File scripts\install_kiwoom_theme_once_shutdown_task.ps1 -Uninstall"
