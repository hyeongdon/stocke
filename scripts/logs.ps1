# Stocke 로그 보기
# logs.bat | logs.bat follow | logs.bat server | logs.bat caddy

param(
    [Parameter(Position = 0)]
    [ValidateSet('app', 'follow', 'server', 'caddy', 'list')]
    [string]$Action = 'app',
    [Parameter(Position = 1)]
    [int]$Lines = 50
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Initialize-LogConsoleEncoding {
    try {
        if ($env:OS -match 'Windows') { chcp 65001 | Out-Null }
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding = $utf8
        [Console]::InputEncoding = $utf8
        $script:OutputEncoding = $utf8
    } catch {
        # ignore
    }
}
Initialize-LogConsoleEncoding

$Python = Join-Path $Root 'venv\Scripts\python.exe'
function Get-AppLogPath {
    if (-not (Test-Path $Python)) {
        return Join-Path $Root 'stock_pipeline.log'
    }
    $path = & $Python -c "from core.config import Config; print(Config.LOG_FILE)" 2>$null
    if (-not $path) { return Join-Path $Root 'stock_pipeline.log' }
    if ([System.IO.Path]::IsPathRooted($path)) { return $path }
    return Join-Path $Root $path
}

function Show-Tail([string]$Path, [int]$N, [switch]$Wait) {
    if (-not (Test-Path $Path)) {
        Write-Host "파일 없음: $Path" -ForegroundColor Red
        return 1
    }
    Write-Host "--- $Path (last $N lines) ---" -ForegroundColor Cyan
    $readArgs = @{ Path = $Path; Tail = $N; Encoding = 'UTF8' }
    if ($Wait) {
        Get-Content @readArgs -Wait
    } else {
        Get-Content @readArgs
    }
    return 0
}

$appLog = Get-AppLogPath
$serverLog = Join-Path $Root 'logs\server.log'
$caddyLog = Join-Path $Root 'logs\caddy.log'

switch ($Action) {
    'list' {
        Write-Host "앱 로그:   $appLog"
        Write-Host "서버 로그: $serverLog  (server.bat start 시 uvicorn)"
        Write-Host "Caddy:     $caddyLog"
    }
    'app' { exit (Show-Tail $appLog $Lines) }
    'follow' { exit (Show-Tail $appLog $Lines -Wait) }
    'server' { exit (Show-Tail $serverLog $Lines) }
    'caddy' { exit (Show-Tail $caddyLog $Lines) }
}
