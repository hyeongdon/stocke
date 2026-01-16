# 서버 자동화 배포 가이드

## 개요
이 가이드는 Stocke 서버의 자동화된 재시작, 상태 확인, 정리 작업을 위한 시스템을 설정하는 방법을 설명합니다.

## 파일 구조
```
/home/ubuntu/project/stocke/
├── restart_server.sh      # 서버 재시작 스크립트
├── health_check.sh        # 서버 상태 확인 스크립트
├── setup_cron.sh          # Cron Job 설정 스크립트
├── deploy_automation.md   # 이 가이드 파일
└── logs/                  # 로그 파일 디렉토리
    ├── restart.log        # 재시작 로그
    ├── health_check.log   # 상태 확인 로그
    └── cron.log          # Cron 작업 로그
```

## 설정 방법

### 1. 실행 권한 부여
```bash
cd /home/ubuntu/project/stocke
chmod +x restart_server.sh
chmod +x health_check.sh
chmod +x setup_cron.sh
```

### 2. Cron Job 설정
```bash
./setup_cron.sh
```

### 3. 수동 테스트
```bash
# 서버 재시작 테스트
./restart_server.sh

# 상태 확인 테스트
./health_check.sh
```

## 자동화 작업 스케줄

| 시간 | 작업 | 설명 |
|------|------|------|
| 매 30분 | 서버 상태 확인 | 프로세스, 응답, API 엔드포인트 확인 |
| 매일 01:00 | 만료된 관심종목 정리 | 이전 날 조건식 종목들 정리 |
| 매일 02:00 | 서버 재시작 | 최신 코드 반영 및 메모리 정리 |
| 매주 일요일 03:00 | 로그 파일 정리 | 7일 이상 된 로그 파일 삭제 |
| 매월 1일 04:00 | 데이터베이스 백업 | SQLite 데이터베이스 백업 |

## 로그 확인

### 실시간 로그 모니터링
```bash
# 재시작 로그
tail -f /home/ubuntu/project/stocke/logs/restart.log

# 상태 확인 로그
tail -f /home/ubuntu/project/stocke/logs/health_check.log

# Cron 작업 로그
tail -f /home/ubuntu/project/stocke/logs/cron.log
```

### 로그 파일 위치
- `logs/restart.log`: 서버 재시작 관련 로그
- `logs/health_check.log`: 서버 상태 확인 로그
- `logs/cron.log`: 모든 Cron 작업 로그

## 수동 작업

### 서버 재시작
```bash
cd /home/ubuntu/project/stocke
./restart_server.sh
```

### 서버 상태 확인
```bash
cd /home/ubuntu/project/stocke
./health_check.sh
```

### 만료된 관심종목 정리
```bash
curl -X POST http://localhost:8001/watchlist/sync/cleanup
```

## 문제 해결

### Cron Job 확인
```bash
# 현재 설정된 Cron 작업 확인
crontab -l

# Cron 서비스 상태 확인
sudo systemctl status cron
```

### 로그 확인
```bash
# 시스템 로그에서 Cron 관련 오류 확인
sudo tail -f /var/log/syslog | grep CRON
```

### 수동 실행 테스트
```bash
# 각 스크립트를 수동으로 실행하여 문제 확인
./restart_server.sh
./health_check.sh
```

## 주의사항

1. **백업**: 중요한 데이터는 별도로 백업하세요.
2. **모니터링**: 로그를 정기적으로 확인하여 문제를 조기에 발견하세요.
3. **테스트**: 새로운 설정을 적용하기 전에 테스트 환경에서 먼저 확인하세요.
4. **권한**: 스크립트 실행 권한이 올바르게 설정되었는지 확인하세요.

## 업데이트

코드가 업데이트되면 다음 명령으로 최신 버전을 적용할 수 있습니다:
```bash
cd /home/ubuntu/project/stocke
git pull origin main
./restart_server.sh
```
