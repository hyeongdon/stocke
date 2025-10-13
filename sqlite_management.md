# SQLite 데이터베이스 관리 가이드

## 📋 개요
이 가이드는 Stocke 프로젝트의 SQLite 데이터베이스(`stock_pipeline.db`)를 직접 관리하는 방법을 설명합니다.

## 🗄️ 데이터베이스 정보
- **파일명**: `stock_pipeline.db`
- **위치**: 프로젝트 루트 디렉토리
- **경로**: `C:\Users\Administrator\project\stocke\stock_pipeline.db`

## 🔧 SQLite 접속 방법

### 1. 명령줄 도구 사용 (권장)

#### A. SQLite3 설치 확인
```bash
# SQLite3 설치 확인
sqlite3 --version
```

#### B. 데이터베이스 접속
```bash
# 프로젝트 디렉토리로 이동
cd C:\Users\Administrator\project\stocke

# SQLite 데이터베이스 접속
sqlite3 stock_pipeline.db
```

#### C. 기본 명령어
```sql
-- 테이블 목록 확인
.tables

-- 테이블 구조 확인
.schema pending_buy_signals

-- 헤더 표시 설정
.headers on

-- 컬럼 모드 설정
.mode column

-- 종료
.quit
```

### 2. Python 스크립트 사용

#### A. Python 스크립트 생성
```python
# sqlite_manager.py
import sqlite3
import os

def connect_to_db():
    """SQLite 데이터베이스 연결"""
    db_path = os.path.join(os.path.dirname(__file__), 'stock_pipeline.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 딕셔너리 형태로 결과 반환
    return conn

def execute_query(query, params=None):
    """쿼리 실행"""
    conn = connect_to_db()
    cursor = conn.cursor()
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        if query.strip().upper().startswith('SELECT'):
            results = cursor.fetchall()
            return [dict(row) for row in results]
        else:
            conn.commit()
            return cursor.rowcount
    except Exception as e:
        print(f"오류: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

# 사용 예시
if __name__ == "__main__":
    # 테이블 목록 조회
    tables = execute_query("SELECT name FROM sqlite_master WHERE type='table';")
    print("테이블 목록:", tables)
    
    # pending_buy_signals 테이블 구조 확인
    schema = execute_query("PRAGMA table_info(pending_buy_signals);")
    print("테이블 구조:", schema)
```

#### B. 스크립트 실행
```bash
python sqlite_manager.py
```

### 3. GUI 도구 사용

#### A. DB Browser for SQLite (추천)
1. [DB Browser for SQLite](https://sqlitebrowser.org/) 다운로드
2. 설치 후 실행
3. `stock_pipeline.db` 파일 열기

#### B. SQLiteStudio
1. [SQLiteStudio](https://sqlitestudio.pl/) 다운로드
2. 설치 후 실행
3. 데이터베이스 추가 → `stock_pipeline.db` 선택

## 🗑️ pending_buy_signals 테이블 초기화

### 1. 전체 데이터 삭제 (테이블 구조 유지)

#### A. 명령줄에서 실행
```bash
# SQLite 접속
sqlite3 stock_pipeline.db

# 모든 데이터 삭제
DELETE FROM pending_buy_signals;

# 삭제 확인
SELECT COUNT(*) FROM pending_buy_signals;

# 종료
.quit
```

#### B. Python 스크립트로 실행
```python
# clear_pending_signals.py
import sqlite3
import os

def clear_pending_signals():
    """pending_buy_signals 테이블의 모든 데이터 삭제"""
    db_path = os.path.join(os.path.dirname(__file__), 'stock_pipeline.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 삭제 전 개수 확인
        cursor.execute("SELECT COUNT(*) FROM pending_buy_signals")
        count_before = cursor.fetchone()[0]
        print(f"삭제 전 레코드 수: {count_before}")
        
        # 모든 데이터 삭제
        cursor.execute("DELETE FROM pending_buy_signals")
        
        # 삭제 후 개수 확인
        cursor.execute("SELECT COUNT(*) FROM pending_buy_signals")
        count_after = cursor.fetchone()[0]
        print(f"삭제 후 레코드 수: {count_after}")
        
        conn.commit()
        print(f"✅ {count_before}개의 레코드가 삭제되었습니다.")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    clear_pending_signals()
```

### 2. 특정 조건으로 데이터 삭제

#### A. PENDING 상태만 삭제
```sql
-- 명령줄에서 실행
DELETE FROM pending_buy_signals WHERE status = 'PENDING';

-- 확인
SELECT COUNT(*) FROM pending_buy_signals WHERE status = 'PENDING';
```

#### B. 특정 날짜 이전 데이터 삭제
```sql
-- 7일 이전 데이터 삭제
DELETE FROM pending_buy_signals 
WHERE detected_at < datetime('now', '-7 days');

-- 확인
SELECT COUNT(*) FROM pending_buy_signals 
WHERE detected_at < datetime('now', '-7 days');
```

#### C. 특정 종목만 삭제
```sql
-- 특정 종목 코드 삭제
DELETE FROM pending_buy_signals WHERE stock_code = '005930';

-- 확인
SELECT * FROM pending_buy_signals WHERE stock_code = '005930';
```

### 3. 테이블 완전 삭제 (구조까지 삭제)

```sql
-- 테이블 완전 삭제
DROP TABLE pending_buy_signals;

-- 테이블 재생성 (필요한 경우)
CREATE TABLE pending_buy_signals (
    id INTEGER PRIMARY KEY,
    condition_id INTEGER NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(100) NOT NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detected_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    signal_type VARCHAR(20) NOT NULL DEFAULT 'condition',
    failure_reason VARCHAR(255),
    reference_candle_high INTEGER,
    reference_candle_date DATETIME,
    target_price INTEGER,
    UNIQUE(detected_date, condition_id, stock_code)
);
```

## 📊 유용한 쿼리 모음

### 1. 데이터 조회

#### A. 전체 데이터 조회
```sql
-- 모든 데이터 조회
SELECT * FROM pending_buy_signals ORDER BY detected_at DESC;

-- 최근 10개만 조회
SELECT * FROM pending_buy_signals ORDER BY detected_at DESC LIMIT 10;
```

#### B. 상태별 조회
```sql
-- PENDING 상태만 조회
SELECT * FROM pending_buy_signals WHERE status = 'PENDING';

-- 상태별 개수 조회
SELECT status, COUNT(*) as count 
FROM pending_buy_signals 
GROUP BY status;
```

#### C. 종목별 조회
```sql
-- 특정 종목 조회
SELECT * FROM pending_buy_signals WHERE stock_code = '005930';

-- 종목별 개수 조회
SELECT stock_code, stock_name, COUNT(*) as count 
FROM pending_buy_signals 
GROUP BY stock_code, stock_name 
ORDER BY count DESC;
```

### 2. 통계 조회

#### A. 일별 통계
```sql
-- 일별 신호 개수
SELECT detected_date, COUNT(*) as count 
FROM pending_buy_signals 
GROUP BY detected_date 
ORDER BY detected_date DESC;
```

#### B. 시간별 통계
```sql
-- 시간별 신호 개수
SELECT strftime('%H', detected_at) as hour, COUNT(*) as count 
FROM pending_buy_signals 
GROUP BY hour 
ORDER BY hour;
```

### 3. 데이터 정리

#### A. 오래된 데이터 정리
```sql
-- 30일 이전 데이터 삭제
DELETE FROM pending_buy_signals 
WHERE detected_at < datetime('now', '-30 days');

-- 완료된 신호만 정리
DELETE FROM pending_buy_signals 
WHERE status IN ('ORDERED', 'FAILED') 
AND detected_at < datetime('now', '-7 days');
```

#### B. 중복 데이터 정리
```sql
-- 중복 데이터 확인
SELECT condition_id, stock_code, detected_date, COUNT(*) as count 
FROM pending_buy_signals 
GROUP BY condition_id, stock_code, detected_date 
HAVING COUNT(*) > 1;

-- 중복 데이터 삭제 (최신 것만 유지)
DELETE FROM pending_buy_signals 
WHERE id NOT IN (
    SELECT MAX(id) 
    FROM pending_buy_signals 
    GROUP BY condition_id, stock_code, detected_date
);
```

## 🔄 백업 및 복원

### 1. 데이터베이스 백업
```bash
# 전체 데이터베이스 백업
sqlite3 stock_pipeline.db ".backup stock_pipeline_backup_$(date +%Y%m%d).db"

# 특정 테이블만 백업
sqlite3 stock_pipeline.db ".dump pending_buy_signals" > pending_signals_backup.sql
```

### 2. 데이터베이스 복원
```bash
# 백업에서 복원
sqlite3 stock_pipeline.db < pending_signals_backup.sql

# 또는 백업 파일에서 복원
sqlite3 stock_pipeline.db ".restore stock_pipeline_backup_20241010.db"
```

## ⚠️ 주의사항

### 1. 백업 필수
- 데이터 삭제 전에 반드시 백업을 생성하세요
- 중요한 데이터가 손실될 수 있습니다

### 2. 서버 중지
- 데이터베이스 작업 전에 서버를 중지하세요
- 동시 접근으로 인한 오류를 방지할 수 있습니다

### 3. 트랜잭션 사용
- 여러 쿼리를 실행할 때는 트랜잭션을 사용하세요
- 오류 발생 시 롤백할 수 있습니다

```sql
-- 트랜잭션 시작
BEGIN TRANSACTION;

-- 작업 수행
DELETE FROM pending_buy_signals WHERE status = 'PENDING';

-- 확인 후 커밋 또는 롤백
COMMIT;  -- 또는 ROLLBACK;
```

## 🚀 빠른 실행 가이드

### 1. 매수대기 신호 전체 삭제
```bash
# 1. 프로젝트 디렉토리로 이동
cd C:\Users\Administrator\project\stocke

# 2. SQLite 접속
sqlite3 stock_pipeline.db

# 3. 데이터 삭제
DELETE FROM pending_buy_signals;

# 4. 확인
SELECT COUNT(*) FROM pending_buy_signals;

# 5. 종료
.quit
```

### 2. Python 스크립트로 실행
```bash
# 1. 프로젝트 디렉토리로 이동
cd C:\Users\Administrator\project\stocke

# 2. 가상환경 활성화
venv\Scripts\activate

# 3. 스크립트 실행
python clear_pending_signals.py
```

이제 언제든지 SQLite 데이터베이스를 직접 관리할 수 있습니다! 🎉
