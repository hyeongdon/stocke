# OCI Network Security Groups (NSG) 확인 방법

## 📋 NSG 확인 위치 (단계별)

### 방법 1: 인스턴스에서 직접 확인 (가장 쉬운 방법)

#### 단계 1: 인스턴스 찾기
1. **OCI Console 접속**
   - https://cloud.oracle.com
   - 로그인

2. **Compute 메뉴**
   - 왼쪽 상단 햄버거 메뉴(☰) 클릭
   - 또는 왼쪽 메뉴에서 `Compute` 찾기
   - `Compute` → `Instances` 클릭

3. **인스턴스 선택**
   - 인스턴스 목록에서 자신의 인스턴스 찾기
   - 인스턴스 이름 클릭 (예: `instance-20250501-1806`)

#### 단계 2: VNIC 확인
1. **인스턴스 상세 페이지**
   - 인스턴스 상세 페이지가 열림
   - 페이지를 아래로 스크롤

2. **Attached VNICs 섹션 찾기**
   - 페이지 중간 또는 아래쪽에 `Attached VNICs` 섹션이 있음
   - 또는 왼쪽 메뉴에서 `Attached VNICs` 클릭

3. **VNIC 선택**
   - VNIC 이름 클릭 (보통 `primaryvnic` 또는 비슷한 이름)
   - 또는 VNIC 이름 옆의 링크 클릭

#### 단계 3: Network Security Groups 확인
1. **VNIC 상세 페이지**
   - VNIC 상세 페이지가 열림
   - 페이지를 아래로 스크롤

2. **Network Security Groups 섹션 찾기**
   - `Network Security Groups` 섹션 찾기
   - 또는 왼쪽 메뉴에서 `Network Security Groups` 클릭

3. **결과 확인**
   - **NSG가 있는 경우:**
     - NSG 목록이 표시됨
     - NSG 이름이 보임 (예: `nsg-xxxxx`)
   - **NSG가 없는 경우:**
     - "No Network Security Groups" 또는 "None" 표시
     - 빈 목록 표시

### 방법 2: Network Security Groups 메뉴에서 확인

1. **Network Security Groups 목록**
   - 왼쪽 메뉴: `Networking` → `Network Security Groups`
   - 모든 NSG 목록이 표시됨

2. **NSG 상세 확인**
   - NSG 선택 → `Attached Resources` 탭
   - 어떤 인스턴스/VNIC에 연결되어 있는지 확인

## 🔍 NSG 사용 여부 확인

### NSG를 사용하는 경우

**특징:**
- VNIC에 NSG가 연결되어 있음
- Security Lists와 NSG 둘 다 규칙이 통과해야 함
- NSG에 규칙을 추가해야 함

**확인 방법:**
```
Compute → Instances → [인스턴스] → Attached VNICs → [VNIC] → Network Security Groups
```

### NSG를 사용하지 않는 경우

**특징:**
- VNIC에 NSG가 연결되어 있지 않음
- Security Lists만 사용
- Security Lists에만 규칙 추가하면 됨

**확인 방법:**
- VNIC의 Network Security Groups 섹션에 "No Network Security Groups" 표시

## 🛠️ NSG 규칙 추가 방법 (NSG 사용 시)

### 1. NSG 찾기

1. `Networking` → `Network Security Groups`
2. 인스턴스에 연결된 NSG 선택

### 2. Ingress Rules 추가

1. NSG 상세 페이지에서 `Ingress Rules` 탭 클릭
2. `Add Ingress Rules` 버튼 클릭
3. 다음 정보 입력:
   ```
   Stateless: No
   Source Type: CIDR
   Source CIDR: 0.0.0.0/0 (또는 Your_IP/32)
   IP Protocol: TCP
   Source Port Range: All (또는 비워두기)
   Destination Port Range: 5432
   Description: PostgreSQL external access
   ```
4. `Add Ingress Rules` 클릭

## 📝 빠른 확인 체크리스트

- [ ] `Compute` → `Instances` → 인스턴스 선택
- [ ] `Attached VNICs` → VNIC 선택
- [ ] `Network Security Groups` 섹션 확인
- [ ] NSG가 있으면 → NSG 선택 → Ingress Rules 확인
- [ ] NSG가 없으면 → Security Lists만 확인하면 됨

## ⚠️ 중요 사항

### Security Lists vs NSG

**Security Lists:**
- VCN 레벨 설정
- 모든 리소스에 기본 적용
- VCN → Security Lists에서 설정

**Network Security Groups (NSG):**
- 리소스 레벨 설정
- 특정 VNIC에 연결
- 더 세밀한 제어 가능

**둘 다 사용하는 경우:**
- Security Lists 규칙과 NSG 규칙 둘 다 통과해야 함
- 둘 다에 포트 5432 규칙이 있어야 함

## 🔧 문제 해결

### NSG가 있는데 규칙이 없는 경우

1. NSG 선택
2. Ingress Rules 탭
3. 포트 5432 규칙 추가

### NSG가 없는데 접속이 안 되는 경우

1. Security Lists만 확인
2. Security Lists에 포트 5432 규칙이 있는지 확인
3. 서버에서 iptables 규칙 확인

### 둘 다 설정했는데 접속이 안 되는 경우

1. Security Lists 규칙 확인
2. NSG 규칙 확인
3. 서버 iptables 규칙 확인
4. 포트 리스닝 확인 (`sudo ss -tlnp | grep 5432`)

