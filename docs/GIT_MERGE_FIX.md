# Git Merge 충돌 해결 가이드

## 문제 상황

서버에서 `git pull` 시 다음과 같은 오류가 발생:

```
error: Your local changes to the following files would be overwritten by merge:
        health_check.sh
        restart_server.sh
        setup_cron.sh
        stock_pipeline.db
Please commit your changes or stash them before you merge.
```

## 해결 방법

### 방법 1: Stash 사용 (권장)

로컬 변경사항을 임시 저장하고 pull 후 필요시 복원:

```bash
cd /home/ubuntu/project/stocke

# 1. 데이터베이스 백업 (중요!)
mkdir -p backup
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db

# 2. 변경사항 stash
git stash push -m "Auto stash before pull"

# 3. Pull
git pull origin main

# 4. 필요시 stash 복원
git stash pop
```

### 방법 2: Commit 사용

로컬 변경사항을 commit하고 pull:

```bash
cd /home/ubuntu/project/stocke

# 1. 데이터베이스 백업
mkdir -p backup
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db

# 2. 변경사항 commit
git add health_check.sh restart_server.sh setup_cron.sh
git commit -m "Update server scripts"

# 3. Pull
git pull origin main
```

### 방법 3: 자동화 스크립트 사용

제공된 스크립트 사용:

```bash
cd /home/ubuntu/project/stocke

# 실행 권한 부여 (최초 1회)
chmod +x shell/fix_git_merge.sh

# Stash 방식 (기본값, 권장)
./shell/fix_git_merge.sh --stash

# Commit 방식
./shell/fix_git_merge.sh --commit

# 변경사항 버리기 (주의!)
./shell/fix_git_merge.sh --discard
```

## 각 방법의 장단점

### Stash 방식 (권장)
- ✅ 로컬 변경사항 보존
- ✅ 나중에 복원 가능
- ✅ 커밋 히스토리 깔끔
- ⚠️ 복원 시 충돌 가능

### Commit 방식
- ✅ 변경사항 영구 보존
- ✅ 히스토리 추적 가능
- ⚠️ 불필요한 커밋 생성 가능

### Discard 방식
- ✅ 빠른 해결
- ❌ 로컬 변경사항 영구 삭제
- ❌ 복원 불가능

## 데이터베이스 파일 처리

`stock_pipeline.db`는 데이터베이스 파일이므로:

1. **항상 백업**: pull 전에 반드시 백업
2. **병합 주의**: 데이터베이스는 자동 병합 불가
3. **수동 확인**: pull 후 데이터베이스 상태 확인

```bash
# 백업
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db

# Pull 후 데이터베이스 확인
sqlite3 stock_pipeline.db "SELECT COUNT(*) FROM positions;"
```

## 자동화 스크립트 상세

`shell/fix_git_merge.sh` 스크립트는 다음을 수행:

1. ✅ 현재 상태 확인
2. ✅ 데이터베이스 자동 백업
3. ✅ 선택한 방식으로 변경사항 처리
4. ✅ Git pull 실행
5. ✅ 최종 상태 확인

## 주의사항

1. **데이터베이스 백업 필수**: `stock_pipeline.db`는 항상 백업
2. **서버 재시작**: pull 후 서버 재시작 권장
3. **상태 확인**: pull 후 `./check_deployment.sh` 실행

## 빠른 해결 (한 줄 명령)

```bash
cd /home/ubuntu/project/stocke && \
mkdir -p backup && \
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db && \
git stash push -m "Auto stash $(date +%Y%m%d_%H%M%S)" && \
git pull origin main && \
./restart_server.sh
```

## 빠른 Pull 스크립트 사용 (가장 간단)

```bash
cd /home/ubuntu/project/stocke

# 실행 권한 부여 (최초 1회)
chmod +x shell/quick_pull.sh

# 자동으로 stash하고 pull
./shell/quick_pull.sh
```

이 스크립트는 다음을 자동으로 수행합니다:
1. ✅ 데이터베이스 자동 백업
2. ✅ 로컬 변경사항 자동 stash
3. ✅ Git pull 실행
4. ✅ Stash 복원 여부 선택 (대화형)

## Merge 충돌이 이미 발생한 경우

만약 이미 merge가 진행 중이고 충돌이 발생한 경우:

### 오류 메시지
```
error: Pulling is not possible because you have unmerged files.
hint: Fix them up in the work tree, and then use 'git add/rm <file>'
hint: as appropriate to mark resolution and make a commit.
fatal: Exiting because of an unresolved conflict.
```

### 해결 방법

#### 방법 1: 자동화 스크립트 사용 (권장)

```bash
cd /home/ubuntu/project/stocke

# 실행 권한 부여 (최초 1회)
chmod +x shell/resolve_merge_conflict.sh

# 로컬 변경사항 유지 (서버 설정 우선)
./shell/resolve_merge_conflict.sh --ours

# 또는 원격 변경사항 사용 (GitHub 우선)
./shell/resolve_merge_conflict.sh --theirs
```

#### 방법 2: 수동 해결

```bash
cd /home/ubuntu/project/stocke

# 1. 충돌 파일 확인
git status

# 2. 데이터베이스 백업
mkdir -p backup
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db

# 3. 로컬 변경사항 유지 (서버 설정 우선)
git checkout --ours health_check.sh restart_server.sh setup_cron.sh
git add health_check.sh restart_server.sh setup_cron.sh

# 4. 데이터베이스는 로컬 버전 유지 (백업했으므로)
# stock_pipeline.db는 git add 하지 않음

# 5. Merge 완료
git commit -m "Resolve merge conflict - keep local changes"

# 6. 서버 재시작
./restart_server.sh
```

#### 방법 3: 원격 변경사항 사용

```bash
cd /home/ubuntu/project/stocke

# 1. 데이터베이스 백업
mkdir -p backup
cp stock_pipeline.db backup/stock_pipeline_$(date +%Y%m%d_%H%M%S).db

# 2. 원격 변경사항 사용
git checkout --theirs health_check.sh restart_server.sh setup_cron.sh
git add health_check.sh restart_server.sh setup_cron.sh

# 3. Merge 완료
git commit -m "Resolve merge conflict - use remote changes"

# 4. 서버 재시작
./restart_server.sh
```

#### 방법 4: Merge 취소 후 재시도

```bash
cd /home/ubuntu/project/stocke

# 1. Merge 취소
git merge --abort

# 2. 변경사항 stash
git stash push -m "Auto stash before pull"

# 3. Pull 재시도
git pull origin main

# 4. 서버 재시작
./restart_server.sh
```

## Git 사용자 정보 설정 오류 해결

### 오류 메시지
```
Author identity unknown
*** Please tell me who you are.
fatal: unable to auto-detect email address
```

### 해결 방법

#### 방법 1: 자동화 스크립트 사용 (권장)

```bash
cd /home/ubuntu/project/stocke

# 실행 권한 부여 (최초 1회)
chmod +x shell/setup_git_config.sh

# 기본값으로 설정 (Stocke Server / server@stocke.local)
./shell/setup_git_config.sh

# 또는 사용자 지정
./shell/setup_git_config.sh "Your Name" "your@email.com"
```

#### 방법 2: 수동 설정

```bash
cd /home/ubuntu/project/stocke

# 로컬 설정 (이 저장소에만 적용)
git config user.name "Stocke Server"
git config user.email "server@stocke.local"

# 또는 전역 설정 (모든 저장소에 적용)
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

#### 방법 3: 한 줄 명령어

```bash
cd /home/ubuntu/project/stocke && \
git config user.name "Stocke Server" && \
git config user.email "server@stocke.local" && \
git commit -m "Resolve merge conflict"
```

### 확인

```bash
# 설정 확인
git config user.name
git config user.email

# 모든 설정 확인
git config --list
```

