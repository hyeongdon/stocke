# Stocke 서버 관리 (Windows)
# 사용: server.bat start | stop | restart | status | tray

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'tray')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'

function Initialize-ConsoleEncoding {
    try {
        # Windows 콘솔은 UTF-8로 통일 (PS 5.1 + cmd 배치 호출 포함)
        if ($env:OS -match 'Windows') {
            chcp 65001 | Out-Null
        }
        $enc = [System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding = $enc
        [Console]::InputEncoding = $enc
        $script:OutputEncoding = $enc
    } catch {
        # ignore
    }
}
Initialize-ConsoleEncoding

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PidFile = Join-Path $Root 'logs\server.pid'
$LogFile = Join-Path $Root 'logs\server.log'
$TrayScript = Join-Path $Root 'scripts\server_tray.ps1'
$Python = Join-Path $Root 'venv\Scripts\python.exe'

function Get-EnvServerSetting {
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

function Get-ServerPort {
    return [int](Get-EnvServerSetting -Name 'PORT' -Default '8000')
}

function Get-ServerHost {
    return Get-EnvServerSetting -Name 'HOST' -Default '127.0.0.1'
}

function Get-ListenerPids([int]$Port) {
    $pids = @()
    $pattern = ":$Port\s"
    netstat -ano | Select-String $pattern | Select-String 'LISTENING' | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ -ne '' }
        if ($parts.Count -ge 1) {
            $procId = $parts[-1]
            if ($procId -match '^\d+$' -and [int]$procId -gt 0) {
                $pids += [int]$procId
            }
        }
    }
    return ($pids | Select-Object -Unique)
}

function Test-ServerHealth([int]$Port) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-ServerHealth([int]$Port, [int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServerHealth $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Stop-StockeServer([int]$Port) {
    $stopped = $false

    if (Test-Path $PidFile) {
        $savedPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($savedPid -match '^\d+$') {
            $proc = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "PID $savedPid 종료 중..."
                Stop-Process -Id ([int]$savedPid) -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    foreach ($procId in (Get-ListenerPids $Port)) {
        Write-Host "포트 $Port 점유 프로세스(PID $procId) 종료 중..."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }

    if (-not $stopped) {
        Write-Host "실행 중인 서버가 없습니다."
    } else {
        Start-Sleep -Seconds 2
        Write-Host "서버 종료 완료."
    }
}

function Start-StockeServer([int]$Port, [string]$BindHost) {
    if (-not (Test-Path $Python)) {
        Write-Host "venv가 없습니다. 먼저 shell\install.bat 을 실행하세요." -ForegroundColor Red
        exit 1
    }

    $existing = Get-ListenerPids $Port
    if ($existing.Count -gt 0) {
        Write-Host "포트 $Port 가 이미 사용 중입니다 (PID: $($existing -join ', '))." -ForegroundColor Yellow
        Write-Host "재시작하려면: server.bat restart"
        exit 1
    }

    if (-not (Test-Path (Split-Path $LogFile))) {
        New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
    }

    $screenerLimit = Get-EnvServerSetting -Name 'SCREENER_CANDIDATE_LIMIT' -Default '20'
    $env:SCREENER_CANDIDATE_LIMIT = $screenerLimit

    $args = @('-m', 'uvicorn', 'core.main:app', '--host', $BindHost, '--port', "$Port")
    Write-Host "서버 시작: http://${BindHost}:$Port (bind=$BindHost)"
    Write-Host "스크리너 후보: 거래대금 상위 $screenerLimit 종목"
    Write-Host "로컬: http://127.0.0.1:$Port/dashboard"
    if ($BindHost -eq '0.0.0.0') {
        Write-Host "외부(LAN): http://<이PC_LAN_IP>:$Port/dashboard" -ForegroundColor Yellow
    }
    Write-Host "로그: $LogFile"

    $proc = Start-Process -FilePath $Python `
        -ArgumentList $args `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardError $LogFile

    $proc.Id | Out-File -FilePath $PidFile -Encoding ascii

    Write-Host "시작 대기 중..."
    if (Wait-ServerHealth $Port) {
        $listen = Get-ListenerPids $Port
        Write-Host "서버 시작 완료 (PID $($listen -join ', '))" -ForegroundColor Green
    } else {
        Write-Host "프로세스는 실행됐지만 30초 내 응답 없음. logs\server.log 를 확인하세요." -ForegroundColor Yellow
    }
}

function Show-Status([int]$Port) {
    $pids = Get-ListenerPids $Port
    if ($pids.Count -eq 0) {
        Write-Host "상태: 중지됨 (포트 $Port)"
        return
    }

    $healthy = Test-ServerHealth $Port
    $state = if ($healthy) { '실행 중 (응답 OK)' } else { '실행 중 (응답 없음/시작 중)' }
    Write-Host "상태: $state"
    Write-Host "포트: $Port"
    Write-Host "PID:  $($pids -join ', ')"
    Write-Host "URL:  http://127.0.0.1:$Port/dashboard"
    if (Test-Path $PidFile) {
        Write-Host "PID 파일: $(Get-Content $PidFile -ErrorAction SilentlyContinue)"
    }
}

function Start-StockeTray {
    if (-not (Test-Path $TrayScript)) {
        Write-Host "트레이 스크립트가 없습니다: $TrayScript" -ForegroundColor Yellow
        return
    }
    # 이미 떠 있으면 server_tray.ps1 이 mutex로 즉시 종료
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @(
            '-NoProfile',
            '-STA',
            '-WindowStyle', 'Hidden',
            '-ExecutionPolicy', 'Bypass',
            '-File', $TrayScript
        ) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden | Out-Null
    Write-Host "시스템 트레이 아이콘을 띄웠습니다. (작업표시줄 우측)" -ForegroundColor Cyan
}

$port = Get-ServerPort
$bindHost = Get-ServerHost

switch ($Action) {
    'start'   { Start-StockeServer $port $bindHost; Start-StockeTray }
    'stop'    { Stop-StockeServer $port }
    'restart' { Stop-StockeServer $port; Start-StockeServer $port $bindHost; Start-StockeTray }
    'status'  { Show-Status $port }
    'tray'    { Start-StockeTray }
}
