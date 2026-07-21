# Windows 작업 스케줄러 - 장 마감 후 테마/키워드 전체 매핑 배치
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_theme_batch_task.ps1
#       powershell ... -At "18:00" -Uninstall

param(
  [string]$TaskName = "stocke-theme-mart-batch",
  [string]$At = "18:00",
  [ValidateSet("Daily", "Hourly")]
  [string]$Mode = "Daily",
  [string]$EndAt = "15:30",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) { $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (!(Test-Path $pythonExe)) { $pythonExe = "python" }

$scriptPath = Join-Path $projectRoot "scripts\theme_mart_batch.py"
if (!(Test-Path $scriptPath)) {
  throw "배치 스크립트를 찾을 수 없습니다: $scriptPath"
}

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
  Write-Host "작업 제거 완료: $TaskName"
  exit 0
}

$batchArgs = "`"$scriptPath`" --top-n 0 --no-news"
$taskCommand = "`"$pythonExe`" $batchArgs"

if ($Mode -eq "Daily") {
  try {
    $atTime = [DateTime]::ParseExact($At, "HH:mm", $null)
  } catch {
    throw "시간 형식 오류. 예: -At `"18:00`""
  }

  $action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $batchArgs `
    -WorkingDirectory $projectRoot

  $trigger = New-ScheduledTaskTrigger -Daily -At $atTime

  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

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
    -Description "stocke 테마/키워드 전체 매핑 (장후 1회) + 텔레그램 일일 리포트" `
    -Force | Out-Null

  Write-Host "작업 등록 완료(Daily): $TaskName ($At)"
  Write-Host "  command: $pythonExe $batchArgs"
  Write-Host "  log: $projectRoot\logs\theme_mart_batch.log"
}
else {
  # 레거시: 장중 1시간 주기 (부하 큼 — Daily 권장)
  $null = & schtasks.exe /Create /TN $TaskName /TR $taskCommand /SC HOURLY /MO 1 /ST $At /ET $EndAt /F 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "schtasks 등록 실패 (exit=$LASTEXITCODE)"
  }
  Write-Host "작업 등록 완료(Hourly): $TaskName ($At~$EndAt, 1시간 간격)"
}
