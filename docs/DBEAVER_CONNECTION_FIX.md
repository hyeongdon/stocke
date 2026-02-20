# DBeaver 연결 오류 해결 가이드

## ❌ 일반적인 오류

### 오류 1: "Unable to parse URL jdbc:postgresql://http://..."

**원인:** Host 필드에 `http://`가 포함되어 있음

**해결 방법:**

1. DBeaver 연결 설정에서:
   - **Host**: `144.24.81.83` (IP 주소만, http:// 없이)
   - **Port**: `5432`
   - **Database**: `stocke_db`
   - **Username**: `stocke_user`
   - **Password**: 비밀번호

2. 또는 JDBC URL을 직접 수정:
   - 잘못된 형식: `jdbc:postgresql://http://144.24.81.83:5432/stocke_db`
   - 올바른 형식: `jdbc:postgresql://144.24.81.83:5432/stocke_db`

### 오류 2: "Connection refused"

**원인:** 
- 포트가 열려있지 않음
- 방화벽 차단
- 컨테이너가 실행되지 않음

**해결 방법:**

```bash
# 서버에서 확인
docker ps | grep postgres-stocke

# 포트 확인
docker port postgres-stocke

# 방화벽 확인
sudo ufw status

# 방화벽 열기
sudo ufw allow 5432/tcp
```

### 오류 3: "Password authentication failed"

**원인:** 잘못된 비밀번호

**해결 방법:**

```bash
# 서버에서 비밀번호 확인
cat docker-compose.yml | grep POSTGRES_PASSWORD
# 또는
cat .env | grep POSTGRES_PASSWORD
```

### 오류 4: "Connection timeout"

**원인:**
- 클라우드 보안 그룹에서 포트가 차단됨
- 네트워크 문제

**해결 방법:**
- AWS/GCP/Azure 보안 그룹에서 인바운드 규칙 확인
- 포트 5432가 열려있는지 확인

## ✅ 올바른 DBeaver 설정

### 단계별 설정

1. **새 연결 생성**
   - `Database` → `New Database Connection`
   - `PostgreSQL` 선택

2. **Main 탭 설정**
   ```
   Host: 144.24.81.83
   Port: 5432
   Database: stocke_db
   Username: stocke_user
   Password: [비밀번호 입력]
   ```

3. **테스트 연결**
   - `Test Connection` 클릭
   - 성공하면 "Connected" 메시지 표시

### JDBC URL 확인

연결 설정 후 `Edit Connection` → `Driver properties`에서 JDBC URL을 확인:

**올바른 형식:**
```
jdbc:postgresql://144.24.81.83:5432/stocke_db
```

**잘못된 형식:**
```
jdbc:postgresql://http://144.24.81.83:5432/stocke_db  ❌
jdbc:postgresql://https://144.24.81.83:5432/stocke_db  ❌
jdbc:postgresql://144.24.81.83/:5432/stocke_db  ❌ (슬래시 위치)
```

## 🔍 연결 정보 확인 스크립트

서버에서 실행:

```bash
cd ~/project/stocke
chmod +x scripts/get_postgresql_connection_info.sh
./scripts/get_postgresql_connection_info.sh
```

이 스크립트는 다음 정보를 출력합니다:
- 서버 IP 주소
- 포트 번호
- 데이터베이스 이름
- 사용자 이름
- 비밀번호 위치

## 🧪 연결 테스트

### 서버에서 테스트

```bash
# 로컬에서 테스트
docker exec postgres-stocke pg_isready -U stocke_user -d stocke_db

# 외부 접속 테스트 (다른 컴퓨터에서)
telnet 144.24.81.83 5432
# 또는
nc -zv 144.24.81.83 5432
```

### Python으로 테스트

```python
import psycopg2

try:
    conn = psycopg2.connect(
        host="144.24.81.83",
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

## 📝 체크리스트

연결 전 확인사항:

- [ ] Host 필드에 `http://` 없이 IP만 입력
- [ ] Port가 `5432`로 설정됨
- [ ] Database 이름이 `stocke_db`로 설정됨
- [ ] Username이 `stocke_user`로 설정됨
- [ ] Password가 올바르게 입력됨
- [ ] 서버에서 컨테이너가 실행 중 (`docker ps | grep postgres`)
- [ ] 방화벽에서 포트 5432가 열려있음
- [ ] 클라우드 보안 그룹에서 포트가 허용됨

## 🔐 보안 팁

1. **SSH 터널링 사용** (권장)
   - DBeaver 연결 설정 → SSH 탭
   - SSH를 통해 터널링하면 포트를 외부에 노출하지 않음

2. **특정 IP만 허용**
   - 클라우드 보안 그룹에서 특정 IP만 허용
   - 방화벽에서 특정 IP만 허용

3. **강력한 비밀번호 사용**







