# 조건식 실시간 편입 알림 — 꺼져 있을 때만 기동 (이미 --realtime 이면 스킵)
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\ensure_condition_realtime_alert.ps1
#   powershell ... -MorningWindowOnly -WindowStart "08:50" -WindowEnd "09:10"
param(
    [switch]$MorningWindowOnly,
    [string]$WindowStart = '08:50',
    [string]$WindowEnd = '09:10'
)

$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogFile = Join-Path $Root 'logs\scheduler_run.log'
$AlertLog = Join-Path $Root 'logs\condition_telegram_alert.log'
$Python = Join-Path $Root 'venv\Scripts\python.exe'
$Script = Join-Path $Root 'scripts\condition_telegram_alert.py'
$Needle = 'condition_telegram_alert.py'

function Write-SchedLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    $dir = Split-Path $LogFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-MorningWindow {
    param([string]$Start, [string]$End)
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

function Test-RealtimeAlertRunning {
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
        foreach ($p in @($procs)) {
            $cmd = [string]$p.CommandLine
            if (-not $cmd) { continue }
            if ($cmd -notmatch [regex]::Escape($Needle)) { continue }
            if ($cmd -match '(^|\s)--realtime(\s|$)') { return $true }
        }
    } catch {
        Write-SchedLog "WARN: process check failed: $($_.Exception.Message)"
    }
    return $false
}

Write-SchedLog '=== ensure_condition_realtime_alert ==='

if ($MorningWindowOnly) {
    if (-not (Test-MorningWindow -Start $WindowStart -End $WindowEnd)) {
        Write-SchedLog "SKIP: outside morning window ($WindowStart-$WindowEnd, weekday only)"
        exit 0
    }
    Write-SchedLog "morning window OK ($WindowStart-$WindowEnd)"
}

if (-not (Test-Path $Script)) {
    Write-SchedLog "ERROR: script not found: $Script"
    exit 1
}

if (-not (Test-Path $Python)) {
    $Python = 'python'
    Write-SchedLog 'WARN: venv python missing, fallback to system python'
}

if (Test-RealtimeAlertRunning) {
    Write-SchedLog 'SKIP: realtime alert already running (--realtime)'
    exit 0
}

$dir = Split-Path $AlertLog
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

Write-SchedLog "START: $Python $Script --realtime"
try {
    # 로그는 condition_telegram_alert.py 의 RotatingFileHandler 가 기록
    $p = Start-Process -FilePath $Python `
        -ArgumentList @("`"$Script`"", '--realtime') `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Write-SchedLog "OK: started pid=$($p.Id) (log=$AlertLog)"
    Start-Sleep -Seconds 3
    if (Test-RealtimeAlertRunning) {
        Write-SchedLog 'OK: realtime process confirmed'
        exit 0
    }
    Write-SchedLog 'WARN: process not confirmed after start (check condition_telegram_alert.log)'
    exit 0
} catch {
    Write-SchedLog "ERROR: start failed: $($_.Exception.Message)"
    exit 1
}
