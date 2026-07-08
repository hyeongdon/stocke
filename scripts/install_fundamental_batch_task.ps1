# Windows 작업 스케줄러 — 매일 지정 시각에 기본적분석 마트 배치 실행
# 사용: powershell -ExecutionPolicy Bypass -File scripts\install_fundamental_batch_task.ps1
#       powershell ... -Time "18:00" -Uninstall

param(
    [string]$Time = "18:00",
    [string]$TaskName = "Stocke-FundamentalBatch",
    [switch]$Uninstall
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BatchFile = Join-Path $Root 'run_fundamental_batch.bat'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "작업 제거됨: $TaskName" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $BatchFile)) {
    Write-Host "run_fundamental_batch.bat 을 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

try {
    $at = [DateTime]::ParseExact($Time, 'HH:mm', $null)
} catch {
    Write-Host "시간 형식 오류. 예: -Time `"18:00`"" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute 'cmd.exe' `
    -Argument "/c `"$BatchFile`"" `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Daily -At $at

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

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
    -Description "Stocke 기본적분석 마트 배치 매일 $Time 실행 (네이버 → DB)" `
    -Force | Out-Null

Write-Host ""
Write-Host "등록 완료: $TaskName" -ForegroundColor Green
Write-Host "  시각: 매일 $Time"
Write-Host "  배치: $BatchFile"
Write-Host "  로그: $Root\logs\fundamental_mart_batch.log"
Write-Host ""
Write-Host "확인: 작업 스케줄러 → $TaskName"
Write-Host "제거: powershell -File scripts\install_fundamental_batch_task.ps1 -Uninstall"
Write-Host "즉시 테스트: $BatchFile"
Write-Host ""
