# PostgreSQL 외부 접속 설정 가이드

## 📋 개요
DBeaver, pgAdmin, TablePlus 등 외부 도구로 PostgreSQL에 접속하는 방법을 설명합니다.

## 🔧 Docker 설정 확인

### 1. 포트 매핑 확인

현재 `docker-compose.yml`에서 포트가 올바르게 매핑되어 있는지 확인:

```yaml
ports:
  - "${POSTGRES_PORT:-5432}:5432"  # 호스트:컨테이너
```

이 설정이 있으면 호스트의 5432 포트로 접속 가능합니다.

### 2. 포트 확인

```bash
# 포트 매핑 확인
docker ps | grep postgres-stocke

# 또는
docker port postgres-stocke
```

**예상 결과:**
```
5432/tcp -> 0.0.0.0:5432
```

## 🌐 외부 접속 설정

### 방법 1: 현재 설정 그대로 사용 (권장)

현재 Docker 설정으로 이미 외부 접속이 가능합니다. 다음 정보를 사용하세요:

**접속 정보:**
- **호스트 (Host)**: 서버 IP 주소 또는 도메인
- **포트 (Port)**: `5432`
- **데이터베이스 (Database)**: `stocke_db`
- **사용자 (User)**: `stocke_user`
- **비밀번호 (Password)**: docker-compose.yml에 설정한 비밀번호

### 방법 2: 특정 IP만 허용하도록 설정

보안을 위해 특정 IP만 허용하려면:

```yaml
# docker-compose.yml
services:
  postgres:
    ports:
      - "서버IP:5432:5432"  # 예: "192.168.1.100:5432:5432"
```

### 방법 3: 방화벽 설정 (Ubuntu)

```bash
# UFW 방화벽 사용 시
sudo ufw allow 5432/tcp
sudo ufw status

# 또는 특정 IP만 허용
sudo ufw allow from YOUR_IP_ADDRESS to any port 5432
```

## 🔌 DBeaver 접속 설정

### 1. DBeaver에서 새 연결 생성

1. **새 연결 생성**
   - DBeaver 실행
   - 상단 메뉴: `Database` → `New Database Connection`
   - 또는 `Alt+Shift+N`

2. **PostgreSQL 선택**
   - 데이터베이스 목록에서 `PostgreSQL` 선택
   - `Next` 클릭

3. **접속 정보 입력**
   ```
   Host: 서버IP주소 (예: 123.456.789.0)
   Port: 5432
   Database: stocke_db
   Username: stocke_user
   Password: docker-compose.yml에 설정한 비밀번호
   ```

4. **테스트 연결**
   - `Test Connection` 버튼 클릭
   - 드라이버가 없으면 자동으로 다운로드됨

5. **연결 완료**
   - `Finish` 클릭

### 2. 연결 문자열 (Connection String)

**⚠️ 중요: DBeaver에서는 Host 필드에 IP 주소만 입력하세요!**

DBeaver 연결 설정:
- **Host**: `144.24.81.83` (http:// 없이 IP만)
- **Port**: `5432`
- **Database**: `stocke_db`
- **Username**: `stocke_user`
- **Password**: 비밀번호

**잘못된 예시:**
```
Host: http://144.24.81.83  ❌
JDBC URL: jdbc:postgresql://http://144.24.81.83:5432/stocke_db  ❌
          jdbc:postgresql://http://144.24.81.83:5432/stocke_db
```

**올바른 예시:**
```
Host: 144.24.81.83  ✅
JDBC URL: jdbc:postgresql://144.24.81.83:5432/stocke_db  ✅
```

연결 문자열 형식 (참고용):
```
jdbc:postgresql://서버IP:5432/stocke_db
```

**주의:** `http://` 또는 `https://`를 포함하지 마세요!

## 🔐 보안 설정

### 1. 비밀번호 확인

```bash
# docker-compose.yml에서 확인
cat docker-compose.yml | grep POSTGRES_PASSWORD

# 또는 .env 파일에서
cat .env | grep POSTGRES_PASSWORD
```

### 2. PostgreSQL 접속 허용 설정 (필요시)

Docker PostgreSQL 이미지는 기본적으로 모든 호스트에서 접속을 허용합니다. 
더 엄격한 설정이 필요하면:

```yaml
# docker-compose.yml에 추가
services:
  postgres:
    command:
      - "postgres"
      - "-c"
      - "listen_addresses=*"  # 모든 IP에서 접속 허용
```

## 📊 서버 IP 주소 확인

### 서버에서 IP 확인

```bash
# 공인 IP 확인 (외부에서 접속할 때 사용)
curl ifconfig.me
# 또는
curl ipinfo.io/ip

# 로컬 IP 확인
hostname -I
# 또는
ip addr show
```

### 클라우드 서버인 경우

- **AWS EC2**: 퍼블릭 IP 또는 Elastic IP 사용
- **GCP**: 외부 IP 주소 사용
- **Azure**: 퍼블릭 IP 주소 사용

## 🔥 방화벽 설정

### Ubuntu UFW

```bash
# PostgreSQL 포트 열기
sudo ufw allow 5432/tcp

# 상태 확인
sudo ufw status

# 특정 IP만 허용 (보안 강화)
sudo ufw allow from YOUR_IP_ADDRESS to any port 5432
```

### AWS Security Group

1. AWS Console → EC2 → Security Groups
2. 인바운드 규칙 추가:
   - Type: PostgreSQL
   - Port: 5432
   - Source: My IP (또는 특정 IP)

### GCP Firewall Rules

```bash
# 방화벽 규칙 추가
gcloud compute firewall-rules create allow-postgres \
    --allow tcp:5432 \
    --source-ranges YOUR_IP_ADDRESS/32 \
    --description "Allow PostgreSQL"
```

## 🧪 접속 테스트

### 1. 로컬에서 테스트

```bash
# 서버에서 직접 테스트
psql -h localhost -p 5432 -U stocke_user -d stocke_db
```

### 2. 외부에서 테스트 (telnet 또는 nc)

```bash
# 다른 컴퓨터에서
telnet 서버IP 5432
# 또는
nc -zv 서버IP 5432
```

### 3. Python 스크립트로 테스트

```python
import psycopg2

try:
    conn = psycopg2.connect(
        host="서버IP",
        port=5432,
        database="stocke_db",
        user="stocke_user",
        password="비밀번호"
    )
    print("✅ 연결 성공!")
    conn.close()
except Exception as e:
    print(f"❌ 연결 실패: {e}")
```

## 🛠️ 문제 해결

### 문제 1: "Connection refused"

**원인:** 포트가 열려있지 않거나 컨테이너가 실행되지 않음

**해결:**
```bash
# 컨테이너 확인
docker ps | grep postgres

# 포트 확인
docker port postgres-stocke

# 방화벽 확인
sudo ufw status
```

### 문제 2: "Password authentication failed"

**원인:** 잘못된 비밀번호

**해결:**
```bash
# 비밀번호 확인
cat docker-compose.yml | grep POSTGRES_PASSWORD

# 비밀번호 재설정
docker exec -it postgres-stocke psql -U postgres -c "ALTER USER stocke_user WITH PASSWORD 'new_password';"
```

### 문제 3: "Connection timeout" (오라클 클라우드 + Docker 환경)

**원인:** 오라클 클라우드의 강력한 내부 방화벽(iptables)이 트래픽을 차단하거나, Docker의 `FORWARD` 체인이 막혀있음.

**해결:**
오라클 클라우드 웹 콘솔(Security Lists)에서 포트를 열어도 OS 내부에서 차단되는 경우가 99%입니다. 서버 터미널에서 다음을 실행하여 방화벽을 강제로 엽니다.

```bash
# 1. 외부에서 들어오는 INPUT 허용 (1번 자리에 우선 삽입)
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 5432 -j ACCEPT

# 2. Docker 컨테이너로 전달되는 FORWARD 허용 (매우 중요 ⭐)
sudo iptables -I FORWARD 1 -j ACCEPT

# 3. 로컬 루프백 인터페이스 허용
sudo iptables -I INPUT 1 -i lo -j ACCEPT

# 4. 설정 영구 저장 (재부팅 시 유지)
# (오류 발생 시: sudo apt-get install iptables-persistent -y 설치 후 실행)
sudo netfilter-persistent save
```

### 문제 4: "No route to host"

**원인:** 서버에 도달할 수 없음

**해결:**
- 서버 IP 주소 확인
- 네트워크 연결 확인
- 서버가 실행 중인지 확인

## 📝 빠른 참조

### 접속 정보 요약

```
호스트: 서버IP주소
포트: 5432
데이터베이스: stocke_db
사용자: stocke_user
비밀번호: docker-compose.yml에 설정한 값
```

### DBeaver 연결 문자열

```
jdbc:postgresql://서버IP:5432/stocke_db
```

### 명령어로 접속 정보 확인

```bash
# 서버 IP 확인
curl ifconfig.me

# 포트 확인
docker port postgres-stocke

# 비밀번호 확인
cat docker-compose.yml | grep POSTGRES_PASSWORD
```

## 🔒 보안 권장사항

1. **강력한 비밀번호 사용**
2. **특정 IP만 허용** (가능한 경우)
3. **SSH 터널링 사용** (더 안전)
4. **SSL 연결 사용** (프로덕션 환경)
5. **정기적인 비밀번호 변경**

## 🔐 SSH 터널링 (더 안전한 방법)

외부에서 직접 접속하는 대신 SSH 터널을 사용할 수 있습니다:

### DBeaver에서 SSH 터널 설정

1. DBeaver 연결 설정에서 `SSH` 탭 선택
2. SSH 설정:
   - Host: 서버 IP
   - Port: 22
   - User: ubuntu (또는 서버 사용자)
   - Authentication: Private Key 또는 Password
3. Main 탭에서:
   - Host: `localhost` (SSH 터널을 통해)
   - Port: `5432`

이 방법은 PostgreSQL 포트를 외부에 노출하지 않아 더 안전합니다.

