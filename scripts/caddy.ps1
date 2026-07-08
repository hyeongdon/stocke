# Caddy reverse proxy: hyungdon.store :80 -> localhost:8000
# Usage: proxy.bat install | start | stop | restart | status

param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'start', 'stop', 'restart', 'status')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = $MyInvocation.MyCommand.Path
Set-Location $Root

$CaddyDir = Join-Path $Root 'tools\caddy'
$CaddyExe = Join-Path $CaddyDir 'caddy.exe'
$Caddyfile = Join-Path $Root 'deploy\caddy\Caddyfile'
$DataDir = Join-Path $Root 'deploy\caddy\data'
$ConfigDir = Join-Path $Root 'deploy\caddy\config'
$PidFile = Join-Path $Root 'logs\caddy.pid'
$LogFile = Join-Path $Root 'logs\caddy.log'
$CaddyVersion = '2.9.1'

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Admin([string]$why) {
    if (-not (Test-IsAdmin)) {
        Write-Host ""
        Write-Host "=== Admin required: $why ===" -ForegroundColor Yellow
        Write-Host "Click Yes on UAC, then proxy will start." -ForegroundColor Yellow
        Write-Host ""
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$ScriptPath`"", $Action
        )
        exit 0
    }
}

function Get-ListenerPids([int]$Port) {
    $pids = @()
    netstat -ano | Select-String ":$Port\s" | Select-String 'LISTENING' | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ -ne '' }
        if ($parts.Count -ge 1) {
            $procId = $parts[-1]
            if ($procId -match '^\d+$' -and [int]$procId -gt 0) { $pids += [int]$procId }
        }
    }
    return ($pids | Select-Object -Unique)
}

function Install-Caddy {
    if (Test-Path $CaddyExe) {
        Write-Host "Caddy OK: $CaddyExe"
        & $CaddyExe version
        return
    }
    New-Item -ItemType Directory -Path $CaddyDir -Force | Out-Null
    $zip = Join-Path $env:TEMP "caddy_${CaddyVersion}_windows_amd64.zip"
    $url = "https://github.com/caddyserver/caddy/releases/download/v${CaddyVersion}/caddy_${CaddyVersion}_windows_amd64.zip"
    Write-Host "Downloading Caddy $CaddyVersion ..."
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $CaddyDir -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Write-Host "Installed: $CaddyExe"
    & $CaddyExe version
}

function Ensure-FirewallRules {
    $name = 'Stocke Caddy 80'
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow | Out-Null
        Write-Host "Firewall rule added: $name"
    }
}

function Test-AppBackend {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/docs' -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Wait-PortListen([int]$Port, [int]$TimeoutSec = 20) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ListenerPids $Port).Count -gt 0) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Show-CaddyLogTail {
    if (Test-Path $LogFile) {
        Write-Host ""
        Write-Host "--- logs\caddy.log (last 15 lines) ---"
        Get-Content $LogFile -Tail 15 -ErrorAction SilentlyContinue
        Write-Host "---"
    }
}

function Clear-StaleTlsLocks {
    $locks = Join-Path $DataDir 'caddy\locks'
    if (Test-Path $locks) {
        Get-ChildItem $locks -Filter '*.lock' -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Pause-IfElevated {
    if (Test-IsAdmin) {
        Write-Host ""
        Write-Host "Press Enter to close this window ..."
        Read-Host | Out-Null
    }
}

function Start-CaddyProxy {
    Require-Admin 'bind port 80 (reverse proxy)'
    Install-Caddy
    Ensure-FirewallRules

    if (-not (Test-AppBackend)) {
        Write-Host ""
        Write-Host "ERROR: stocke is not on port 8000." -ForegroundColor Red
        Write-Host "Run first: server.bat start" -ForegroundColor Yellow
        exit 1
    }

    $busy80 = Get-ListenerPids 80
    foreach ($procId in $busy80) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $isCaddy = $p -and $p.Path -like '*\tools\caddy\caddy.exe'
        if (-not $isCaddy) {
            Write-Host "ERROR: Port 80 in use by PID $procId ($($p.ProcessName))" -ForegroundColor Red
            Write-Host "Stop that program or change its port, then retry." -ForegroundColor Yellow
            Pause-IfElevated
            exit 1
        }
    }

    Stop-CaddyProxy -Quiet
    Clear-StaleTlsLocks

    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path $LogFile) -Force | Out-Null
    if (Test-Path $LogFile) { Remove-Item $LogFile -Force -ErrorAction SilentlyContinue }

    $env:XDG_DATA_HOME = $DataDir
    $env:XDG_CONFIG_HOME = $ConfigDir

    Write-Host ""
    Write-Host "Starting Caddy (80 -> 127.0.0.1:8000) ..."
    Write-Host "Log: $LogFile"

    $proc = Start-Process -FilePath $CaddyExe `
        -ArgumentList @('run', '--config', $Caddyfile, '--adapter', 'caddyfile') `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardError $LogFile

    $proc.Id | Out-File -FilePath $PidFile -Encoding ascii

    $ok80 = Wait-PortListen 80

    if (-not $proc.HasExited -and $ok80) {
        $h = Test-ProxyHealth
        Write-Host ""
        Write-Host "Caddy started PID $($proc.Id)" -ForegroundColor Green
        Write-Host "  Port 80  OK  -> http://hyungdon.store" -ForegroundColor Green
        if ($h.http) {
            Write-Host "  Health   OK  (proxy -> stocke:8000)" -ForegroundColor Green
        } else {
            Write-Host "  Health   WARN (port open but backend check failed)" -ForegroundColor Yellow
        }
        Write-Host ""
        Write-Host "Open: http://hyungdon.store  (hosts file required at home)"
        Write-Host "Do NOT use https:// — this setup is HTTP only."
    } else {
        Write-Host ""
        Write-Host "ERROR: Caddy did not bind port 80." -ForegroundColor Red
        if ($proc.HasExited) {
            Write-Host "Process exited (code $($proc.ExitCode))." -ForegroundColor Red
        }
        Show-CaddyLogTail
        Pause-IfElevated
        exit 1
    }
    Pause-IfElevated
}

function Stop-CaddyProxy {
    param([switch]$Quiet)
    $stopped = $false
    if (Test-Path $PidFile) {
        $saved = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($saved -match '^\d+$') {
            Stop-Process -Id ([int]$saved) -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    foreach ($port in @(80, 443)) {
        foreach ($procId in (Get-ListenerPids $port)) {
            $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($p -and $p.Path -like '*\tools\caddy\caddy.exe') {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        }
    }
    if (-not $Quiet) {
        if ($stopped) { Write-Host 'Caddy stopped.' -ForegroundColor Green }
        else { Write-Host 'Caddy was not running.' }
    }
}

function Test-ProxyHealth {
    $ok80 = $false
    try {
        $r = Invoke-WebRequest -Uri 'http://hyungdon.store/docs' -UseBasicParsing -TimeoutSec 8
        $ok80 = ($r.StatusCode -eq 200)
    } catch { }
    return @{ http = $ok80 }
}

function Show-CaddyStatus {
    Write-Host '--- Caddy proxy (HTTP 80 -> 8000) ---'
    if (Test-Path $CaddyExe) { & $CaddyExe version } else { Write-Host 'Not installed. Run: proxy.bat install' }
    foreach ($port in @(80, 8000)) {
        $pids = Get-ListenerPids $port
        if ($pids.Count) { Write-Host "Port $port LISTENING PID $($pids -join ', ')" }
        else { Write-Host "Port $port - (not running)" }
    }
    if ((Get-ListenerPids 80).Count -gt 0) {
        $h = Test-ProxyHealth
        if ($h.http) {
            Write-Host "Health: OK  -> http://hyungdon.store works" -ForegroundColor Green
        } else {
            Write-Host "Health: WARN (port 80 open but http://hyungdon.store failed)" -ForegroundColor Yellow
            Write-Host "Check hosts file: 192.168.x.x hyungdon.store" -ForegroundColor Yellow
        }
    } else {
        Write-Host ""
        Write-Host ">>> Caddy is NOT running <<<" -ForegroundColor Red
        Write-Host ""
        Write-Host "  proxy.bat start" -ForegroundColor Cyan
        Write-Host "  (UAC 창에서 [예] 클릭 — 관리자 권한 필요)" -ForegroundColor Yellow
        Write-Host ""
        if ((Get-ListenerPids 8000).Count -eq 0) {
            Write-Host "Also run: server.bat start  (stocke on port 8000)" -ForegroundColor Yellow
        }
    }
    Show-CaddyLogTail
}

switch ($Action) {
    'install' { Install-Caddy }
    'start'   { Start-CaddyProxy }
    'stop'    { Stop-CaddyProxy }
    'restart' { Stop-CaddyProxy; Start-Sleep 1; Start-CaddyProxy }
    'status'  { Show-CaddyStatus }
}
