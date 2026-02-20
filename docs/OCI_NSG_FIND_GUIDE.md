# OCI Network Security Groups (NSG) 찾기 가이드

## 🔍 NSG 확인 방법 (단계별)

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
   - 아래쪽으로 스크롤

2. **Attached VNICs 섹션 찾기**
   - 페이지 중간 또는 아래쪽에 `Attached VNICs` 섹션이 있음
   - 또는 왼쪽 메뉴에서 `Attached VNICs` 클릭

3. **VNIC 선택**
   - VNIC 이름 클릭 (보통 `primaryvnic` 또는 비슷한 이름)
   - 또는 VNIC 이름 옆의 링크 클릭

#### 단계 3: Network Security Groups 확인
1. **VNIC 상세 페이지**
   - VNIC 상세 페이지가 열림

2. **Network Security Groups 섹션 찾기**
   - 페이지를 아래로 스크롤
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

#### 단계 1: Network Security Groups 메뉴 찾기
1. **OCI Console 접속**
   - https://cloud.oracle.com
   - 로그인

2. **Networking 메뉴**
   - 왼쪽 상단 햄버거 메뉴(☰) 클릭
   - `Networking` 찾기
   - `Networking` → `Network Security Groups` 클릭

#### 단계 2: NSG 목록 확인
1. **NSG 목록**
   - 모든 NSG 목록이 표시됨
   - NSG가 없으면 빈 목록

2. **NSG 상세 확인**
   - NSG가 있다면 선택
   - `Attached Resources` 탭 클릭
   - 어떤 인스턴스/VNIC에 연결되어 있는지 확인

## 📍 OCI Console 경로 요약

### NSG 확인 경로 (인스턴스에서)
```
OCI Console
  → Compute (왼쪽 메뉴)
    → Instances
      → [인스턴스 선택]
        → Attached VNICs (페이지 중간 또는 왼쪽 메뉴)
          → [VNIC 선택]
            → Network Security Groups (페이지 중간 또는 왼쪽 메뉴)
```

### NSG 확인 경로 (직접)
```
OCI Console
  → Networking (왼쪽 메뉴)
    → Network Security Groups
```

## 🔍 NSG가 없는 경우

**NSG가 없다면:**
- Security Lists만 사용하는 경우
- Security Lists에만 규칙 추가하면 됨
- NSG 설정은 불필요

**확인 방법:**
- VNIC의 Network Security Groups 섹션에 "No Network Security Groups" 또는 "None" 표시
- 또는 빈 목록

## 🔍 NSG가 있는 경우

**NSG가 있다면:**
- Security Lists와 NSG 둘 다 규칙이 통과해야 함
- NSG에도 포트 5432 규칙을 추가해야 함

**설정 방법:**
1. NSG 선택
2. `Ingress Rules` 탭 클릭
3. `Add Ingress Rules` 클릭
4. 다음 정보 입력:
   ```
   Source Type: CIDR
   Source CIDR: 0.0.0.0/0 (또는 Your_IP/32)
   IP Protocol: TCP
   Destination Port Range: 5432
   Description: PostgreSQL external access
   ```
5. `Add Ingress Rules` 클릭

## 📸 화면에서 찾는 방법

### 인스턴스 상세 페이지에서
1. 페이지를 아래로 스크롤
2. "Resources" 또는 "Related Resources" 섹션 찾기
3. "Attached VNICs" 또는 "VNICs" 클릭
4. VNIC 이름 클릭
5. VNIC 상세 페이지에서 "Network Security Groups" 섹션 찾기

### 왼쪽 메뉴에서
- 인스턴스 상세 페이지의 왼쪽에 메뉴가 있음
- "Attached VNICs" 메뉴 클릭
- VNIC 선택 후 왼쪽 메뉴에서 "Network Security Groups" 클릭

## ⚠️ 중요 사항

### Security Lists vs NSG

**둘 다 확인해야 하는 경우:**
- NSG가 연결되어 있으면 Security Lists와 NSG 둘 다에 규칙이 있어야 함
- 하나라도 없으면 외부 접속이 안 됨

**Security Lists만 확인하면 되는 경우:**
- NSG가 없으면 Security Lists에만 규칙 추가하면 됨

## 🛠️ 빠른 확인 방법

서버에서 다음 명령으로도 확인 가능 (OCI CLI 사용 시):

```bash
# OCI CLI가 설치되어 있다면
oci network vnic get --vnic-id [VNIC_OCID] | grep -i "network-security-group"
```

하지만 OCI Console에서 확인하는 것이 가장 확실합니다.

## 💡 팁

- NSG가 없어도 정상입니다
- 대부분의 경우 Security Lists만 사용합니다
- NSG는 더 세밀한 제어가 필요할 때 사용합니다
- NSG가 없다면 Security Lists 설정만 확인하면 됩니다







