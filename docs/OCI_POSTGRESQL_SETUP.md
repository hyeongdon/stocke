# Oracle Cloud Infrastructure (OCI) PostgreSQL 외부 접속 설정

## 📋 개요
Oracle Cloud Infrastructure에서 PostgreSQL에 외부 접속을 허용하는 방법을 설명합니다.

## 🔐 OCI 방화벽 설정

OCI에서는 두 가지 방법으로 방화벽을 설정할 수 있습니다:
1. **Security Lists** (VCN 레벨)
2. **Network Security Groups** (리소스 레벨)

## 방법 1: Security Lists 사용 (권장)

### 단계별 설정

1. **OCI Console 접속**
   - https://cloud.oracle.com 접속
   - 로그인

2. **VCN 찾기**
   - 왼쪽 메뉴: `Networking` → `Virtual Cloud Networks`
   - 인스턴스가 속한 VCN 선택

3. **Security List 선택**
   - VCN 상세 페이지에서 `Security Lists` 클릭
   - `Default Security List` 선택 (또는 인스턴스에 연결된 Security List)

4. **Ingress Rules 추가**
   - `Ingress Rules` 탭 클릭
   - `Add Ingress Rules` 버튼 클릭

5. **규칙 입력**
   ```
   Stateless: No (기본값)
   Source Type: CIDR
   Source CIDR: 0.0.0.0/0 (모든 IP) 또는 Your_IP/32 (특정 IP)
   IP Protocol: TCP
   Source Port Range: All
   Destination Port Range: 5432
   Description: PostgreSQL external access
   ```

6. **규칙 추가**
   - `Add Ingress Rules` 클릭

### 보안 권장사항

**특정 IP만 허용 (권장):**
```
Source CIDR: Your_IP/32
예: 123.45.67.89/32
```

**모든 IP 허용 (개발/테스트용):**
```
Source CIDR: 0.0.0.0/0
```

## 방법 2: Network Security Groups 사용

### NSG 사용 여부 확인

**NSG 확인 방법:**
1. `Compute` → `Instances` → 인스턴스 선택
2. `Attached VNICs` → VNIC 선택
3. `Network Security Groups` 섹션 확인
   - NSG가 연결되어 있으면 목록에 표시됨
   - 없으면 "No Network Security Groups" 표시

### NSG가 있는 경우 설정 방법

1. **NSG 찾기**
   - `Networking` → `Network Security Groups`
   - 인스턴스에 연결된 NSG 선택

2. **Ingress Rules 추가**
   - NSG 상세 페이지에서 `Ingress Rules` 탭 클릭
   - `Add Ingress Rules` 버튼 클릭
   - 다음 정보 입력:
     ```
     Stateless: No
     Source Type: CIDR
     Source CIDR: 0.0.0.0/0 (또는 Your_IP/32)
     IP Protocol: TCP
     Source Port Range: All (또는 비워두기)
     Destination Port Range: 5432
     Description: PostgreSQL external access
     ```
   - `Add Ingress Rules` 클릭

### NSG가 없는 경우

- Security Lists만 사용하는 경우
- Security Lists에만 규칙 추가하면 됨
- NSG 설정은 불필요

### ⚠️ 중요: Security Lists와 NSG 둘 다 사용하는 경우

- Security Lists 규칙과 NSG 규칙 둘 다 통과해야 함
- 둘 다에 포트 5432 규칙이 있어야 함

## 🔍 현재 설정 확인

### OCI Console에서 확인

1. **Security Lists 확인**
   - VCN → Security Lists → Ingress Rules
   - 포트 5432 규칙이 있는지 확인

2. **Network Security Groups 확인**
   - 인스턴스 → Attached VNICs → Network Security Groups
   - 연결된 NSG 확인

### 서버에서 확인

```bash
# iptables 규칙 확인
sudo iptables -L INPUT -n | grep 5432

# 포트 리스닝 확인
sudo ss -tlnp | grep 5432

# 진단 스크립트 실행
cd ~/project/stocke
./scripts/diagnose_connection.sh
```

## 🛠️ 문제 해결

### 문제 1: Security Lists와 NSG 둘 다 설정되어 있음

**해결:** 둘 다 통과해야 하므로, 둘 다에 규칙이 있어야 합니다.

### 문제 2: 규칙을 추가했는데도 접속 안 됨

**확인 사항:**
1. Security Lists의 Ingress Rules에 포트 5432가 있는지
2. NSG를 사용하는 경우, 인스턴스에 NSG가 연결되어 있는지
3. 서버에서 iptables 규칙이 추가되었는지
4. 포트가 0.0.0.0에 바인딩되어 있는지

### 문제 3: 특정 IP만 허용하고 싶음

**설정:**
```
Source CIDR: Your_IP/32
예: 123.45.67.89/32
```

**내 IP 확인:**
- Windows: https://www.whatismyip.com/
- 또는: `curl ifconfig.me` (서버에서)

## 📝 빠른 참조

### OCI Console 경로

**Security Lists:**
```
Networking → Virtual Cloud Networks → [VCN 선택] → Security Lists → [Security List 선택] → Ingress Rules
```

**Network Security Groups:**
```
Networking → Network Security Groups → [NSG 선택] → Ingress Rules
```

**인스턴스에 NSG 연결:**
```
Compute → Instances → [인스턴스 선택] → Attached VNICs → [VNIC 선택] → Edit → Network Security Groups
```

### 규칙 설정 값

```
Source Type: CIDR
Source CIDR: 0.0.0.0/0 (모든 IP) 또는 Your_IP/32 (특정 IP)
IP Protocol: TCP
Destination Port Range: 5432
Description: PostgreSQL external access
```

## ✅ 체크리스트

외부 접속 전 확인사항:

- [ ] OCI Security Lists에 포트 5432 Ingress Rule 추가
- [ ] NSG 사용 시, NSG에도 규칙 추가 및 인스턴스에 연결
- [ ] 서버에서 iptables INPUT 규칙 추가
- [ ] 포트가 0.0.0.0에 바인딩됨 (포트 리스닝 확인)
- [ ] 외부에서 포트 테스트 성공

## 🔒 보안 권장사항

1. **특정 IP만 허용**
   - Source CIDR을 `Your_IP/32`로 설정
   - 동적 IP인 경우 VPN 사용 고려

2. **SSH 터널링 사용** (가장 안전)
   - DBeaver에서 SSH 터널 설정
   - PostgreSQL 포트를 외부에 노출하지 않음

3. **강력한 비밀번호 사용**

4. **정기적인 보안 감사**

## 📚 참고 자료

- [OCI Security Lists 문서](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securitylists.htm)
- [OCI Network Security Groups 문서](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/networksecuritygroups.htm)

