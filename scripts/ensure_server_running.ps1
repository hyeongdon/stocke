# 매일 스케줄 실행용 — 서버가 꺼져 있을 때만 기동 (이미 실행 중이면 스킵, 재기동 없음)
param(
    [switch]$MorningWindowOnly,
    [string]$WindowStart = '08:30',
    [string]$WindowEnd = '08:50'
)

$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogFile = Join-Path $Root 'logs\scheduler_run.log'
$ServerScript = Join-Path $Root 'scripts\server.ps1'
$Python = Join-Path $Root 'venv\Scripts\python.exe'
$NotifyScript = Join-Path $Root 'scripts\notify_server_start.py'

function Write-SchedLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    $dir = Split-Path $LogFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

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

function Test-StockeServerUp {
    param([int]$Port)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-StockeServerStable {
    param([int]$Port, [int]$StableChecks = 2, [int]$MaxAttempts = 15, [int]$DelaySec = 2)
    $streak = 0
    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        if (Test-StockeServerUp $Port) {
            $streak++
            if ($streak -ge $StableChecks) { return $true }
        } else {
            $streak = 0
        }
        Start-Sleep -Seconds $DelaySec
    }
    return $false
}
function Send-StartTelegram {
    param([string]$Reason = 'morning_schedule')
    if (-not (Test-Path $Python)) {
        Write-SchedLog "WARN: venv python not found, skip telegram"
        return
    }
    if (-not (Test-Path $NotifyScript)) {
        Write-SchedLog "WARN: notify_server_start.py not found"
        return
    }
    try {
        $out = & $Python $NotifyScript --reason $Reason 2>&1
        foreach ($line in @($out)) {
            if ($line) { Write-SchedLog "telegram: $line" }
        }
        if ($LASTEXITCODE -eq 0) {
            Write-SchedLog "OK: telegram sent (health verified)"
        } else {
            Write-SchedLog "WARN: telegram notify exit $LASTEXITCODE (server not confirmed)"
        }
    } catch {
        Write-SchedLog "WARN: telegram notify failed: $($_.Exception.Message)"
    }
}

function Test-MorningWindow {    param([string]$Start, [string]$End)
    $now = Get-Date
    if ($now.DayOfWeek -in @('Saturday', 'Sunday')) { return $false }
    try {
        $startT = [DateTime]::ParseExact($Start, 'HH:mm', $null).TimeOfDay
        $endT = [DateTime]::ParseExact($End, 'HH:mm', $null).TimeOfDay
    } catch {
        return $false
    }
    $t = $now.TimeOfDay
    return ($t -ge $startT) -and ($t -le $endT)
}

Write-SchedLog "=== scheduled ensure_server_running ==="

if ($MorningWindowOnly) {
    if (-not (Test-MorningWindow -Start $WindowStart -End $WindowEnd)) {
        Write-SchedLog "SKIP: outside morning window ($WindowStart-$WindowEnd, weekday only)"
        exit 0
    }
    Write-SchedLog "morning window OK ($WindowStart-$WindowEnd)"
}

if (-not (Test-Path $ServerScript)) {
    Write-SchedLog "ERROR: server.ps1 not found"
    exit 1
}

$port = [int](Get-EnvSetting -Name 'PORT' -Default '8000')

if (Test-StockeServerUp $port) {
    Write-SchedLog "SKIP: server already running (port $port health OK, no restart)"
    exit 0
}

try {
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ServerScript start 2>&1
    foreach ($line in @($out)) {
        if ($line) { Write-SchedLog $line }
    }

    if ($LASTEXITCODE -eq 0) {
        if (Wait-StockeServerStable -Port $port) {
            Write-SchedLog "OK: server start completed (stable health)"
            $reason = if ($MorningWindowOnly) { 'morning_schedule' } else { 'scheduled' }
            Send-StartTelegram -Reason $reason
            exit 0
        }
        Write-SchedLog "WARN: start command OK but health check failed (unstable or down)"
        exit 1
    }

    $text = ($out | Out-String)
    # ASCII 패턴 — 한글 문자열은 스케줄러/인코딩에 따라 파서 오류 유발 가능
    if ($text -match '\(PID:\s*\d+') {
        Write-SchedLog "SKIP: server already running (port in use, no restart)"
        exit 0
    }

    Write-SchedLog "WARN: server.ps1 exit code $LASTEXITCODE"
    exit $LASTEXITCODE
} catch {
    Write-SchedLog "ERROR: $($_.Exception.Message)"
    exit 1
}
