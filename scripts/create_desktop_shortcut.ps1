# 바탕화면에 Stocke 실행 바로가기 생성
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Target = Join-Path $Root 'launch_stocke.bat'
$Desktop = [Environment]::GetFolderPath('Desktop')
$LinkPath = Join-Path $Desktop 'Stocke.lnk'

if (-not (Test-Path $Target)) {
    Write-Host "launch_stocke.bat 을 찾을 수 없습니다: $Target" -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($LinkPath)
$shortcut.TargetPath = $Target
$shortcut.WorkingDirectory = $Root
$shortcut.WindowStyle = 1
$shortcut.Description = 'Stocke 자동매매 서버 시작 및 대시보드 열기'
$icon = Join-Path $env:SystemRoot 'System32\shell32.dll'
$shortcut.IconLocation = "$icon,137"
$shortcut.Save()

Write-Host "바로가기 생성: $LinkPath" -ForegroundColor Green
