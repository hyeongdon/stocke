# Windows 작업 스케줄러 — 평일 지정 시각에 기술적분석 마트 배치(주봉 RSI 포함) 실행
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_technical_batch_task.ps1
#       powershell ... -Time "18:30" -Uninstall

param(
    [string]$Time = "18:30",
    [string]$TaskName = "Stocke-TechnicalBatch",
    [switch]$Uninstall
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BatchFile = Join-Path $Root 'run_technical_batch.bat'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "작업 제거됨: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $BatchFile)) {
    Write-Host "run_technical_batch.bat 을 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

try {
    $at = [DateTime]::ParseExact($Time, 'HH:mm', $null)
} catch {
    Write-Host "시간 형식 오류. 예: -Time `"18:30`"" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$BatchFile`"" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $at

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
    -Description "Stocke 기술적분석 마트 배치 (일봉 지표 + 주봉 RSI) 평일 $Time 실행" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  시각: 평일 $Time"
Write-Host "  배치: $BatchFile"
Write-Host "  로그: $Root\logs\technical_mart_batch.log"
Write-Host ""
Write-Host "제거: powershell -File scripts\install_technical_batch_task.ps1 -Uninstall"
Write-Host "즉시 테스트: $BatchFile --limit 20"
Write-Host ""
