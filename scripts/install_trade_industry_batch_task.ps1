# Windows 작업 스케줄러 — 수출입 업종 지표 월배치
# PRD: 관세청 공표(매월 15일경) 이후 권장 → 기본 매월 16일 10:00
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_trade_industry_batch_task.ps1
#   powershell ... -Day 16 -At "10:00"
#   powershell ... -Uninstall

param(
    [string]$TaskName = "stocke-trade-industry-batch",
    [ValidateRange(1, 28)]
    [int]$Day = 16,
    [string]$At = "10:00",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$batPath = Join-Path $projectRoot "run_trade_industry_batch.bat"

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
    throw "시간 형식 오류. 예: -At `"10:00`""
}

# New-ScheduledTaskTrigger -Monthly 는 일부 Windows에서 미지원 → schtasks MONTHLY 사용
$tr = "`"$batPath`""
$create = & schtasks.exe /Create /TN $TaskName /TR $tr /SC MONTHLY /D $Day /ST $At /RL LIMITED /F 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "schtasks 등록 실패 (exit=$LASTEXITCODE): $create"
}

# 배터리·중복실행 설정 보강 (설명은 schtasks 경로에서 생략)
try {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
} catch {
    Write-Host "경고: 작업 설정 보강 실패 (등록은 완료됨): $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  스케줄: 매월 ${Day}일 $At"
Write-Host "  launcher: $batPath"
Write-Host "  log: $projectRoot\logs\trade_industry_batch.log"
Write-Host "  알림: TELEGRAM_* 설정 시 시작/종료/오류 전송"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_trade_industry_batch_task.ps1 -Uninstall"
Write-Host "즉시 테스트: $batPath"
Write-Host ""
