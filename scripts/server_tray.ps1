# Stocke server status — Windows system tray
# Usage: server.bat tray  (also auto-started by server start/restart)
#
# Icon: green=OK · yellow=port open · gray=stopped
# Right-click: dashboard / start·stop·restart / exit tray

#Requires -Version 5.1
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ErrorLog = Join-Path $Root 'logs\tray_error.log'
$CpuAlertLog = Join-Path $Root 'logs\cpu_alert.log'

# CPU alert (env override)
$CpuThreshold = 90
$CpuSustainSec = 60
$CpuCooldownSec = 900
try {
    if ($env:CPU_ALERT_THRESHOLD) { $CpuThreshold = [double]$env:CPU_ALERT_THRESHOLD }
    if ($env:CPU_ALERT_SUSTAIN_SEC) { $CpuSustainSec = [int]$env:CPU_ALERT_SUSTAIN_SEC }
    if ($env:CPU_ALERT_COOLDOWN_SEC) { $CpuCooldownSec = [int]$env:CPU_ALERT_COOLDOWN_SEC }
} catch {}

function Write-TrayError([string]$Message) {
    try {
        $dir = Split-Path $ErrorLog
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -Path $ErrorLog -Value $line -Encoding UTF8
    } catch {}
}

function Write-CpuAlertLog([string]$Message) {
    try {
        $dir = Split-Path $CpuAlertLog
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -Path $CpuAlertLog -Value $line -Encoding UTF8
    } catch {}
}

function Get-CpuPercent {
    try {
        $loads = @(Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -ExpandProperty LoadPercentage)
        if (-not $loads -or $loads.Count -eq 0) { return 0 }
        return [int][math]::Round((($loads | Measure-Object -Average).Average))
    } catch {
        return 0
    }
}

function Send-CpuTelegram([int]$Cpu, [int]$SustainSec) {
    try {
        $py = Join-Path $Root 'venv\Scripts\python.exe'
        if (-not (Test-Path $py)) { $py = Join-Path $Root '.venv\Scripts\python.exe' }
        if (-not (Test-Path $py)) { return }
        $script = Join-Path $Root 'scripts\notify_cpu_alert.py'
        if (-not (Test-Path $script)) { return }
        Start-Process -FilePath $py -ArgumentList @(
            $script, '--cpu', "$Cpu", '--sustain', "$SustainSec"
        ) -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
    } catch {
        Write-TrayError ("cpu telegram spawn failed: {0}" -f $_.Exception.Message)
    }
}

try {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    Add-Type -AssemblyName System.Drawing | Out-Null

    if (-not ([System.Management.Automation.PSTypeName]'StockeTrayIcon').Type) {
        Add-Type -ReferencedAssemblies @('System.Drawing', 'System.Windows.Forms') @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
public static class StockeTrayIcon {
  [DllImport("user32.dll", CharSet = CharSet.Auto)]
  private static extern bool DestroyIcon(IntPtr handle);

  public static Icon Make(Color fill) {
    using (var bmp = new Bitmap(16, 16)) {
      using (var g = Graphics.FromImage(bmp)) {
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Color.Transparent);
        using (var b = new SolidBrush(fill)) {
          g.FillEllipse(b, 1, 1, 13, 13);
        }
        using (var pen = new Pen(Color.FromArgb(220, 255, 255, 255), 1.2f)) {
          g.DrawEllipse(pen, 1, 1, 13, 13);
        }
        using (var font = new Font("Segoe UI", 7f, FontStyle.Bold, GraphicsUnit.Pixel))
        using (var tb = new SolidBrush(Color.White)) {
          g.DrawString("S", font, tb, 3.2f, 1.5f);
        }
      }
      IntPtr h = bmp.GetHicon();
      using (Icon tmp = Icon.FromHandle(h)) {
        Icon clone = (Icon)tmp.Clone();
        DestroyIcon(h);
        return clone;
      }
    }
  }
}
"@
    }

    Set-Location $Root
    $TrayPidFile = Join-Path $Root 'logs\server_tray.pid'
    $ServerScript = Join-Path $Root 'scripts\server.ps1'
    $MutexName = 'Local\StockeServerTray'

    $mutex = $null
    $createdNew = $false
    $mutex = New-Object System.Threading.Mutex($false, $MutexName, [ref]$createdNew)
    $owned = $false
    try {
        $owned = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $owned = $true
    }
    if (-not $owned -and -not $Force) {
        Write-TrayError 'another tray instance already running — exit'
        exit 0
    }

    if (-not (Test-Path (Split-Path $TrayPidFile))) {
        New-Item -ItemType Directory -Path (Split-Path $TrayPidFile) -Force | Out-Null
    }
    $PID | Out-File -FilePath $TrayPidFile -Encoding ascii
    Write-TrayError ("tray started pid={0}" -f $PID)

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
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 3
            return $r.StatusCode -eq 200
        } catch {
            return $false
        }
    }

    function Get-ServerState([int]$Port) {
        $pids = @(Get-ListenerPids $Port)
        if ($pids.Count -eq 0) {
            return @{ Key = 'stopped'; Label = '중지됨'; Color = [System.Drawing.Color]::FromArgb(120, 120, 120); Pids = @() }
        }
        if (Test-ServerHealth $Port) {
            return @{ Key = 'ok'; Label = '실행 중'; Color = [System.Drawing.Color]::FromArgb(34, 160, 80); Pids = $pids }
        }
        return @{ Key = 'starting'; Label = '응답 대기'; Color = [System.Drawing.Color]::FromArgb(210, 160, 30); Pids = $pids }
    }

    function Invoke-ServerAction([string]$Action) {
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ServerScript, $Action) `
            -WorkingDirectory $Root `
            -WindowStyle Minimized
    }

    [System.Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Stocke 트레이'
    $form.ShowInTaskbar = $false
    $form.WindowState = 'Minimized'
    $form.Opacity = 0
    $form.FormBorderStyle = 'FixedToolWindow'
    $form.Size = New-Object System.Drawing.Size(1, 1)

    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Visible = $true
    $notify.Text = 'Stocke'

    $iconCache = @{}
    function Set-TrayAppearance($state, [int]$Port) {
        $key = $state.Key
        if (-not $iconCache.ContainsKey($key)) {
            $iconCache[$key] = [StockeTrayIcon]::Make($state.Color)
        }
        $notify.Icon = $iconCache[$key]
        $pidTxt = if ($state.Pids.Count) { "PID $($state.Pids -join ',')" } else { '-' }
        $tip = "Stocke · $($state.Label)`n:$Port · $pidTxt"
        if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 63) }
        $notify.Text = $tip
    }

    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    $miDash = $menu.Items.Add('대시보드 열기')
    $miStatus = $menu.Items.Add('상태 확인')
    [void]$menu.Items.Add('-')
    $miStart = $menu.Items.Add('서버 시작')
    $miStop = $menu.Items.Add('서버 중지')
    $miRestart = $menu.Items.Add('서버 재시작')
    [void]$menu.Items.Add('-')
    $miExit = $menu.Items.Add('트레이만 종료 (서버 유지)')
    $notify.ContextMenuStrip = $menu

    $script:lastKey = ''
    $script:port = Get-ServerPort
    $script:cpuHighSince = $null
    $script:cpuLastAlertAt = $null
    $script:cpuLastSample = 0

    function Refresh-Tray([switch]$Balloon) {
        $script:port = Get-ServerPort
        $state = Get-ServerState $script:port
        Set-TrayAppearance $state $script:port
        if ($Balloon -and $state.Key -ne $script:lastKey -and $script:lastKey -ne '') {
            $title = 'Stocke'
            $msg = switch ($state.Key) {
                'ok' { "실행 중 · http://127.0.0.1:$($script:port)/dashboard" }
                'starting' { "포트는 열렸지만 응답 대기 ($($script:port))" }
                default { "중지됨 (포트 $($script:port))" }
            }
            $iconType = if ($state.Key -eq 'ok') { [System.Windows.Forms.ToolTipIcon]::Info }
                        elseif ($state.Key -eq 'starting') { [System.Windows.Forms.ToolTipIcon]::Warning }
                        else { [System.Windows.Forms.ToolTipIcon]::Error }
            $notify.ShowBalloonTip(2500, $title, $msg, $iconType)
        }
        $script:lastKey = $state.Key

        # CPU 과부하: 임계치 이상 지속 시 트레이 풍선 + 텔레그램 (쿨다운)
        try {
            $cpu = Get-CpuPercent
            $script:cpuLastSample = $cpu
            $now = Get-Date
            if ($cpu -ge $CpuThreshold) {
                if ($null -eq $script:cpuHighSince) {
                    $script:cpuHighSince = $now
                }
                $sustained = ($now - $script:cpuHighSince).TotalSeconds
                if ($sustained -ge $CpuSustainSec) {
                    $cooled = $true
                    if ($null -ne $script:cpuLastAlertAt) {
                        $cooled = (($now - $script:cpuLastAlertAt).TotalSeconds -ge $CpuCooldownSec)
                    }
                    if ($cooled) {
                        $script:cpuLastAlertAt = $now
                        $tip = "CPU ${cpu}% · ${CpuSustainSec}초 이상 ${CpuThreshold}%+"
                        $notify.ShowBalloonTip(
                            5000,
                            'Stocke · CPU 경고',
                            $tip,
                            [System.Windows.Forms.ToolTipIcon]::Warning
                        )
                        Write-CpuAlertLog $tip
                        Send-CpuTelegram -Cpu $cpu -SustainSec ([int]$sustained)
                        # 같은 고부하 구간에서 연속 스팸 방지: 타이머 리셋
                        $script:cpuHighSince = $now
                    }
                }
            } else {
                $script:cpuHighSince = $null
            }
            # 트레이 툴팁에 최근 CPU 표시 (63자 제한)
            $base = $notify.Text
            if ($base -and $base.Length -lt 50) {
                $withCpu = "$base · CPU ${cpu}%"
                if ($withCpu.Length -le 63) { $notify.Text = $withCpu }
            }
        } catch {
            Write-TrayError ("cpu watch error: {0}" -f $_.Exception.Message)
        }
    }

    $miDash.add_Click({
        Start-Process "http://127.0.0.1:$($script:port)/dashboard"
    })
    $miStatus.add_Click({
        $state = Get-ServerState $script:port
        $pidTxt = if ($state.Pids.Count) { $state.Pids -join ', ' } else { '없음' }
        $notify.ShowBalloonTip(
            4000,
            "Stocke · $($state.Label)",
            "포트 $($script:port)`nPID $pidTxt`nhttp://127.0.0.1:$($script:port)/dashboard",
            [System.Windows.Forms.ToolTipIcon]::Info
        )
        Refresh-Tray
    })
    $miStart.add_Click({ Invoke-ServerAction 'start'; Start-Sleep -Milliseconds 800; Refresh-Tray -Balloon })
    $miStop.add_Click({ Invoke-ServerAction 'stop'; Start-Sleep -Milliseconds 800; Refresh-Tray -Balloon })
    $miRestart.add_Click({ Invoke-ServerAction 'restart'; Start-Sleep -Milliseconds 800; Refresh-Tray -Balloon })
    $miExit.add_Click({
        $notify.Visible = $false
        [System.Windows.Forms.Application]::Exit()
    })
    $notify.add_DoubleClick({
        Start-Process "http://127.0.0.1:$($script:port)/dashboard"
    })

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 5000
    $timer.add_Tick({ Refresh-Tray -Balloon })
    $timer.Start()

    $form.add_Shown({
        $form.Hide()
        Refresh-Tray
        $state = Get-ServerState $script:port
        $notify.ShowBalloonTip(
            2500,
            'Stocke 트레이',
            "상태: $($state.Label) · 포트 $($script:port)`n더블클릭=대시보드 · 우클릭=메뉴",
            [System.Windows.Forms.ToolTipIcon]::Info
        )
    })

    $form.add_FormClosed({
        $timer.Stop()
        $notify.Visible = $false
        $notify.Dispose()
        Remove-Item $TrayPidFile -Force -ErrorAction SilentlyContinue
        foreach ($ic in $iconCache.Values) {
            try { $ic.Dispose() } catch {}
        }
        if ($mutex) {
            try { if ($owned) { $mutex.ReleaseMutex() } } catch {}
            $mutex.Dispose()
        }
    })

    [System.Windows.Forms.Application]::Run($form)
}
catch {
    Write-TrayError ($_ | Out-String)
    throw
}
