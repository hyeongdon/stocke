# PostgreSQL Connection Timeout 해결 가이드

## 🔍 Connection Timeout 원인

Connection timeout은 다음 중 하나일 수 있습니다:
1. 방화벽에서 포트 5432가 차단됨
2. 클라우드 보안 그룹에서 포트가 열려있지 않음
3. PostgreSQL 컨테이너가 외부 접속을 허용하지 않음
4. 네트워크 라우팅 문제

## ✅ 단계별 해결 방법

### 1. 서버에서 컨테이너 상태 확인

```bash
# 컨테이너가 실행 중인지 확인
docker ps | grep postgres-stocke

# 포트 매핑 확인
docker port postgres-stocke

# 예상 결과: 0.0.0.0:5432->5432/tcp
```

### 2. 로컬에서 접속 테스트

```bash
# 서버에서 직접 접속 테스트
docker exec -it postgres-stocke psql -U stocke_user -d stocke_db -c "SELECT 1;"

# 또는
psql -h localhost -p 5432 -U stocke_user -d stocke_db
```

로컬에서 접속이 안 되면 컨테이너 설정 문제입니다.

### 3. 방화벽 확인 및 설정

#### Ubuntu UFW 방화벽

```bash
# 방화벽 상태 확인
sudo ufw status

# 포트 5432 열기
sudo ufw allow 5432/tcp

# 상태 확인
sudo ufw status | grep 5432

# 방화벽 재시작 (필요시)
sudo ufw reload
```

#### iptables 확인 및 설정

```bash
# iptables 규칙 확인
sudo iptables -L INPUT -n | grep 5432

# 포트 열기 (외부 접속 허용)
sudo iptables -I INPUT -p tcp --dport 5432 -j ACCEPT

# 규칙 확인
sudo iptables -L INPUT -n | grep 5432

# 규칙 저장 (재부팅 후에도 유지)
# 방법 1: iptables-persistent 사용
sudo apt-get install iptables-persistent
sudo netfilter-persistent save

# 방법 2: 수동 저장
sudo iptables-save > /etc/iptables/rules.v4
```

**현재 상황:**
- Docker가 자동으로 만든 규칙: `ACCEPT ... 172.18.0.2 ... tcp dpt:5432`
- 이것은 Docker 내부 네트워크만 허용합니다
- **외부 접속을 위해 INPUT 체인에 규칙을 추가해야 합니다**

### 4. 클라우드 보안 그룹 설정

#### AWS EC2

1. AWS Console → EC2 → Security Groups
2. 인스턴스에 연결된 보안 그룹 선택
3. 인바운드 규칙 편집 → 규칙 추가:
   - Type: PostgreSQL
   - Protocol: TCP
   - Port: 5432
   - Source: My IP (또는 0.0.0.0/0 - 모든 IP, 보안상 권장하지 않음)

#### GCP

```bash
# 방화벽 규칙 추가
gcloud compute firewall-rules create allow-postgres \
    --allow tcp:5432 \
    --source-ranges YOUR_IP_ADDRESS/32 \
    --description "Allow PostgreSQL"
```

또는 GCP Console에서:
1. VPC Network → Firewall
2. 방화벽 규칙 만들기
3. 프로토콜: TCP, 포트: 5432

#### Azure

1. Azure Portal → Virtual Machines
2. 네트워킹 → 인바운드 포트 규칙 추가
3. 포트: 5432, 프로토콜: TCP

#### Oracle Cloud Infrastructure (OCI)

OCI에서는 **Security Lists** 또는 **Network Security Groups**를 사용합니다.

**방법 1: Security Lists 사용 (권장)**

1. OCI Console → Networking → Virtual Cloud Networks
2. 인스턴스가 속한 VCN 선택
3. Security Lists → Default Security List 선택
4. Ingress Rules → Add Ingress Rules 클릭
5. 다음 정보 입력:
   - Source Type: `CIDR`
   - Source CIDR: `0.0.0.0/0` (모든 IP) 또는 `Your_IP/32` (특정 IP)
   - IP Protocol: `TCP`
   - Destination Port Range: `5432`
   - Description: `PostgreSQL external access`
6. Add Ingress Rules 클릭

**방법 2: Network Security Groups 사용**

1. OCI Console → Networking → Network Security Groups
2. 인스턴스에 연결된 NSG 선택 (또는 새로 생성)
3. Ingress Rules → Add Ingress Rules 클릭
4. 다음 정보 입력:
   - Source Type: `CIDR`
   - Source CIDR: `0.0.0.0/0` 또는 `Your_IP/32`
   - IP Protocol: `TCP`
   - Destination Port Range: `5432`
   - Description: `PostgreSQL external access`
5. Add Ingress Rules 클릭
6. 인스턴스에 NSG 연결:
   - Compute → Instances → 인스턴스 선택
   - Attached VNICs → VNIC 선택 → Edit
   - Network Security Groups에 NSG 추가

**중요:**
- Security Lists와 NSG 둘 다 설정되어 있으면 둘 다 통과해야 합니다
- 기본적으로 Security Lists를 사용하는 경우가 많습니다
- 특정 IP만 허용하려면 `Your_IP/32` 형식 사용 (예: `123.45.67.89/32`)

### 5. PostgreSQL 외부 접속 허용 확인

Docker PostgreSQL은 기본적으로 외부 접속을 허용하지만, 확인해보세요:

```bash
# 컨테이너 내부에서 PostgreSQL 설정 확인
docker exec postgres-stocke psql -U postgres -c "SHOW listen_addresses;"

# 결과가 '*' 또는 '0.0.0.0'이어야 함
```

### 6. 포트 리스닝 확인

```bash
# 서버에서 포트가 열려있는지 확인
sudo netstat -tlnp | grep 5432
# 또는
sudo ss -tlnp | grep 5432

# 예상 결과: 0.0.0.0:5432 또는 :::5432
```

**중요:** `127.0.0.1:5432`만 보이면 외부 접속이 안 됩니다. `0.0.0.0:5432`여야 합니다.

### 7. 외부에서 포트 접근 테스트

다른 컴퓨터나 온라인 도구로 테스트:

```bash
# 다른 컴퓨터에서
telnet 144.24.81.83 5432
# 또는
nc -zv 144.24.81.83 5432

# 온라인 도구 사용
# https://www.yougetsignal.com/tools/open-ports/
# 또는
# https://canyouseeme.org/
```

포트가 열려있으면 연결이 성공합니다.

## 🔧 docker-compose.yml 확인

포트 매핑이 올바른지 확인:

```yaml
ports:
  - "0.0.0.0:5432:5432"  # 모든 IP에서 접속 허용
  # 또는
  - "5432:5432"  # 기본값 (모든 IP 허용)
```

특정 IP만 허용하려면:
```yaml
ports:
  - "YOUR_IP:5432:5432"  # 특정 IP만 허용
```

## 🧪 종합 테스트 스크립트

서버에서 실행:

```bash
#!/bin/bash
echo "=========================================="
echo "PostgreSQL 외부 접속 테스트"
echo "=========================================="
echo ""

echo "1. 컨테이너 상태:"
docker ps | grep postgres-stocke
echo ""

echo "2. 포트 매핑:"
docker port postgres-stocke
echo ""

echo "3. 포트 리스닝:"
sudo netstat -tlnp | grep 5432 || sudo ss -tlnp | grep 5432
echo ""

echo "4. 방화벽 상태:"
sudo ufw status | grep 5432 || echo "UFW 규칙 없음"
echo ""

echo "5. 로컬 접속 테스트:"
docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db
echo ""

echo "6. 서버 IP:"
curl -s ifconfig.me
echo ""
```

## 🚨 일반적인 문제와 해결책

### 문제 1: 포트가 127.0.0.1에만 바인딩됨

**증상:** `netstat`에서 `127.0.0.1:5432`만 보임

**해결:**
```bash
# docker-compose.yml에서 포트 매핑 확인
# 0.0.0.0:5432:5432로 설정되어 있는지 확인

# 컨테이너 재시작
docker compose restart
```

### 문제 2: 방화벽이 포트를 차단

**증상:** 로컬에서는 접속되지만 외부에서는 안 됨

**해결:**
```bash
sudo ufw allow 5432/tcp
sudo ufw reload
```

### 문제 3: 클라우드 보안 그룹 설정 누락

**증상:** 방화벽은 열려있지만 외부 접속 안 됨

**해결:**
- AWS/GCP/Azure 보안 그룹에서 인바운드 규칙 추가
- 포트 5432, 프로토콜 TCP 허용

### 문제 4: Docker 네트워크 문제

**증상:** 컨테이너는 실행 중이지만 포트가 안 보임

**해결:**
```bash
# 컨테이너 재시작
docker compose down
docker compose up -d

# 네트워크 확인
docker network ls
docker network inspect stocke_stocke-network
```

## 📝 체크리스트

외부 접속 전 확인사항:

- [ ] 컨테이너가 실행 중 (`docker ps | grep postgres`)
- [ ] 포트 매핑이 올바름 (`docker port postgres-stocke`)
- [ ] 포트가 0.0.0.0에 바인딩됨 (`netstat | grep 5432`)
- [ ] 방화벽에서 포트 5432가 열려있음 (`ufw status`)
- [ ] 클라우드 보안 그룹에서 포트가 허용됨
- [ ] 로컬에서 접속 테스트 성공
- [ ] 외부에서 포트 테스트 성공 (`telnet` 또는 `nc`)

## 🔐 보안 권장사항

1. **특정 IP만 허용**
   ```bash
   # 방화벽에서
   sudo ufw allow from YOUR_IP_ADDRESS to any port 5432
   
   # docker-compose.yml에서
   ports:
     - "YOUR_IP:5432:5432"
   ```

2. **SSH 터널링 사용** (가장 안전)
   - DBeaver에서 SSH 터널 설정
   - PostgreSQL 포트를 외부에 노출하지 않음

3. **강력한 비밀번호 사용**

4. **SSL 연결 사용** (프로덕션 환경)

