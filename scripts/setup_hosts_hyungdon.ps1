# hyungdon.store → 이 PC 내부 IP (집 Wi-Fi에서 NAT 루프백 우회)
# 관리자 권한 필요 — UAC 확인창에서 [예] 클릭

$ErrorActionPreference = 'Stop'

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host '관리자 권한으로 다시 실행합니다. UAC에서 [예]를 눌러주세요.' -ForegroundColor Yellow
    $self = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$self`""
    )
    exit 0
}

$hostsPath = Join-Path $env:Windir 'System32\drivers\etc\hosts'
$marker = '# stocke hyungdon.store (local LAN)'

# 현재 PC 내부 IP (Wi-Fi 우선)
$ip = $null
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -match '^192\.168\.' -and $_.PrefixOrigin -ne 'WellKnown'
    } |
    Sort-Object { if ($_.InterfaceAlias -match 'Wi-Fi|Wireless') { 0 } else { 1 } } |
    ForEach-Object { if (-not $ip) { $ip = $_.IPAddress } }

if (-not $ip) {
  $ip = '192.168.219.104'
  Write-Host "내부 IP 자동 감지 실패 — 기본값 $ip 사용" -ForegroundColor Yellow
} else {
  Write-Host "내부 IP: $ip"
}

$block = @(
    ''
    $marker
    "$ip`tthyungdon.store"
    "$ip`twww.hyungdon.store"
)

$content = Get-Content $hostsPath -Raw -Encoding UTF8
if ($content -match [regex]::Escape($marker)) {
    Write-Host '이미 hosts에 등록되어 있습니다. 내용 확인:' -ForegroundColor Green
    Get-Content $hostsPath -Encoding UTF8 | Select-String -Pattern 'hyungdon|stocke hyungdon'
} else {
    Add-Content -Path $hostsPath -Value ($block -join "`r`n") -Encoding ASCII
    Write-Host 'hosts 등록 완료:' -ForegroundColor Green
    Get-Content $hostsPath -Encoding UTF8 | Select-String -Pattern 'hyungdon|stocke hyungdon'
}

ipconfig /flushdns | Out-Null
Write-Host ''
Write-Host 'DNS 캐시를 비웠습니다. 브라우저를 새로고침하세요.' -ForegroundColor Cyan
Write-Host ('PC에서 https://hyungdon.store 또는 http://' + $ip + ':8000 테스트') -ForegroundColor Cyan
Write-Host ''
Write-Host '※ 모바일은 hosts가 안 먹습니다. 휴대폰은 Wi-Fi DNS 수동 설정 또는 내부 IP 북마크가 필요합니다.' -ForegroundColor DarkGray
Read-Host 'Enter 키로 종료'
