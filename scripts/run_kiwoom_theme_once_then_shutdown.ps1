# 키움 테마 1회 수집 후 PC 종료 (점검 종료 대비 새벽용)
# 사용: powershell -ExecutionPolicy Bypass -File scripts\run_kiwoom_theme_once_then_shutdown.ps1
#       powershell ... -NoShutdown   # 종료 생략(테스트)

param(
  [switch]$NoShutdown,
  [int]$ShutdownDelaySec = 90
)

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) { $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (!(Test-Path $pythonExe)) { $pythonExe = "python" }

$scriptPath = Join-Path $projectRoot "scripts\theme_mart_batch.py"
$logFile = Join-Path $projectRoot "logs\theme_mart_batch.log"
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "logs") | Out-Null

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "===== $stamp kiwoom-only once start =====" -Encoding UTF8

& $pythonExe $scriptPath --kiwoom-only --top-n 0
$exitCode = $LASTEXITCODE

$stamp2 = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "===== $stamp2 kiwoom-only once end exit=$exitCode =====" -Encoding UTF8

if ($NoShutdown) {
  Write-Host "NoShutdown — PC 종료 생략 (exit=$exitCode)"
  exit $exitCode
}

$msg = "stocke 키움 테마 배치 종료(exit=$exitCode). ${ShutdownDelaySec}초 후 PC 종료"
Write-Host $msg
# /f: 앱 강제 종료, /t: 대기 초
shutdown.exe /s /t $ShutdownDelaySec /c $msg
exit $exitCode
