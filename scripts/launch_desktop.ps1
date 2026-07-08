# 바탕화면 바로가기용 — 서버 기동 후 대시보드 열기
$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$ServerScript = Join-Path $Root 'scripts\server.ps1'

function Get-EnvSetting {
    param([string]$Name, [string]$Default)
    $envFile = Join-Path $Root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match "^\s*$Name\s*=\s*(.+?)\s*$") {
                return $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $Default
}

function Test-ServerUp([int]$Port) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

$port = [int](Get-EnvSetting -Name 'PORT' -Default '8000')
$dashboardUrl = "http://127.0.0.1:$port/dashboard"

Write-Host ""
Write-Host "  Stocke 자동매매 서버" -ForegroundColor Cyan
Write-Host "  --------------------"
Write-Host ""

if (Test-ServerUp $port) {
    Write-Host "서버가 이미 실행 중입니다." -ForegroundColor Green
} else {
    Write-Host "서버를 시작합니다..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ServerScript start
    if (-not (Test-ServerUp $port)) {
        Write-Host ""
        Write-Host "서버 시작에 실패했거나 아직 준비 중입니다." -ForegroundColor Red
        Write-Host "logs\server.log 를 확인하세요."
        Write-Host ""
        Read-Host "Enter 키를 누르면 종료"
        exit 1
    }
}

Write-Host "대시보드 열기: $dashboardUrl" -ForegroundColor Green
Start-Process $dashboardUrl
Write-Host ""
Write-Host "완료. 이 창은 닫아도 됩니다."
Start-Sleep -Seconds 4
