# PRD: 알파스퀘어 테마 매핑 수집 (3rd source)

> **상태**: Phase 0–4 Done  
> **작성일**: 2026-08-04 · **개정**: 2026-08-04 (Phase 0~4 완료)  
> **대상 시스템**: stocke 테마 마트 (`theme_mart_batch` → `theme_map_store` → 스크리너/종가배팅 enrichment)  
> **관련 코드**: `utils/theme_alphasquare_crawler.py`, `utils/theme_map_store.py`, `scripts/theme_mart_batch.py`, `core/main.py`, `static/theme_map.*`, `notifications/theme_batch_report.py`, `tests/test_alphasquare_theme.py`  
> **선행 문서**: `docs/THEME_STOCK_PIPELINE.md`  
> **선행 패턴**: `utils/theme_kiwoom_crawler.py` + `source=kiwoom_theme` 듀얼소스 저장  
> **스파이크 산출물**: `logs/_alphasquare_theme_catalog.json` (테마 454개 요약)

---

## 0. 한 줄 결론

**네이버·키움에 이어서, 알파스퀘어 내부 API(`api.alphasquare.co.kr`)로 테마↔종목 편입을 한 번 더 수집한다.**  
공개 테마 API는 **로그인 없이** 동작한다. 기존 스키마에 `source=alphasquare_theme`만 추가하면 되고, 스크리너·종가배팅 소비층 변경은 최소화한다.

---

## 1. 배경 · 문제

### 1.1 지금 있는 것

| 소스 | `source` | 모듈 | 한계 |
|------|----------|------|------|
| 네이버 금융 HTML | `naver_theme` | `theme_naver_crawler` | HTS/알파스퀘어와 편입·테마명이 다름 |
| 키움 REST `ka90001`/`ka90002` | `kiwoom_theme` | `theme_kiwoom_crawler` | HTS 계열. 호출 한도·장후 배치 |
| 뉴스 키워드 | `news_title` | `stock_news_daily_batch` | 공식 테마 편입 아님 |
| 수동 | `manual` | 테마맵 업로드 | 커버리지 보완용 |

→ 동일 종목이 소스마다 다른 테마로 붙는 **교차 검증·커버리지 보강**이 필요함.

### 1.2 알파스퀘어가 주는 것 (제품 관점)

알파스퀘어 [테마종목](https://alphasquare.co.kr/home/theme-factor) UI는 대략 다음을 노출한다.

| 데이터 | 설명 | stocke 활용 |
|--------|------|-------------|
| 급상승/전체 테마 목록 | 테마 ID·이름·종목 수·순위/수익률 등 | `theme_tags` |
| 테마별 관련주 | 종목코드·종목명 | `theme_tag_edges` |
| 테마 설명 / KEY POINT | 테마 요약 텍스트 | `ThemeTag.meta` / edge `meta_json` (선택) |
| 편입 사유 | 종목이 왜 그 테마인지 | edge `meta_json.reason` (선택, v1 권장) |
| 기간 수익률 추이 | 1주/1개월/3개월 등 | v1 비필수 (스냅샷 메타로 보관 가능) |

공개 공식 OpenAPI는 없음. 웹 번들(`theme-factor-*.js`)이 부르는 **`https://api.alphasquare.co.kr`** 를 재사용한다. Phase 0에서 경로·스키마·무인증 여부를 **실측 확정**함 (§3.2).

### 1.3 왜 “한 번 더”인가

```
네이버만     → 편입 누락 / 테마명 불일치
키움만       → HTS 중심, 이슈 테마·편입 사유 빈약
알파스퀘어   → 이슈 테마·편입 사유·KEY POINT가 풍부 → 커버리지·설명력 보강
```

종가배팅·스크리너 enrichment는 이미 `get_latest_map_by_codes()`로 소스를 머지한다.  
**수집만 붙이면 표시·최강테마 집계에 자연스럽게 반영**된다.

---

## 2. 제품 목표

### 2.1 Goals

1. **G1** — 알파스퀘어 테마 목록 + 구성종목을 일 1회(또는 테마마트와 동일 스케줄) 스냅샷 수집  
2. **G2** — `source=alphasquare_theme` 로 기존 테이블에 저장 (스키마 파괴적 변경 없음)  
3. **G3** — 테마맵 UI / 스크리너 테마 칩에 알파스퀘어 편입이 네이버·키움과 함께 보이도록 머지  
4. **G4** — (권장) 편입 사유·KEY POINT를 `meta_json`에 보관해 테마맵 상세에서 조회 가능  
5. **G5** — CLI 플래그·배치 상태·실패 알림이 키움 스테이지와 동일한 UX

### 2.2 Non-Goals (1차)

| 비범위 | 이유 |
|--------|------|
| 알파스퀘어 로그인 UI / OAuth 제품화 | 배치용 토큰·쿠키면 충분 |
| 급상승 테마 **실시간** 폴링 (1분) | 레이트·ToS·운영 부담. 일 배치가 1차 |
| 테마 수익률 차트 UI | 메타 저장만 선택, 차트는 이후 |
| 매매 게이트에 알파스퀘어 전용 THRESHOLD | 파이프라인 문서와 동일 — enrichment만 |
| HTML 스크래핑 본경로 | 내부 JSON API 우선. HTML은 fallback 후보만 |
| 알파스퀘어 이용약관 위반을 전제로 한 대량 크롤 | Phase 0에서 호출량·헤더·캐시 정책 고정 |

---

## 3. 가능 여부 · 리스크

| 판정 | 내용 |
|------|------|
| **가능** | Phase 0 실측 완료. 무인증으로 테마 전수 + 구성종목 수집 가능 |
| **주요 리스크** | 비공식 API → 경로/스키마 변경, IP/레이트 제한, ToS |
| **완화** | 일 1회·sleep 0.3~0.5s·실패 시 네이버/키움만 degrade · `Origin`/`Referer` 헤더 유지 |

### 3.1 Phase 0 체크리스트

- [x] 테마 목록·상세·종목 편입 API 경로/메서드 확정 (§3.2)  
- [x] 인증: **공개 테마 조회는 무인증**. 관심테마(`watchthemes`)만 403(로그인 필요) → 배치에는 불필요  
- [x] 필드 매핑표 (§3.3)  
- [x] 호출 수·sleep 산정 (§3.4)  
- [ ] 샘플 100종목 × 네이버/키움 교차 일치율 — **Phase 1 첫 적재 후** 리포트  
- [x] 운영 기준: **개인/내부용 · 일 1회 저빈도 배치** (실시간 1분 폴링 금지)

### 3.2 확정 API (Base: `https://api.alphasquare.co.kr`)

권장 헤더: `Accept: application/json`, `Origin: https://alphasquare.co.kr`, `Referer: https://alphasquare.co.kr/home/theme-factor`

| 용도 | Method | Path | Auth | 비고 |
|------|--------|------|------|------|
| 전체 테마 카탈로그 | GET | `/theme/v2/all-themes` | 없음 | 카테고리 27 · 테마 **454** · ~550KB 1회 |
| 대분류만 | GET | `/theme/v2/big-themes` | 없음 | 보조 |
| 급상승 리더보드 | GET | `/theme/v2/leader-board?limit=N` | 없음 | 선택(메타) |
| 테마 상세(+설명/KEY POINT/시세) | GET | `/theme/v2/themes/{theme_id}` | 없음 | description에 KEY POINT 포함 |
| 테마 구성종목 | GET | `/theme/v2/themes/{theme_id}/stocks` | 없음 | **code(6자리)+내부 id** · reason 없음 |
| 종목→테마(+편입사유) | GET | `/theme/v3/themes-for-stock?stock_id={내부id}` | 없음 | **reason 필드 있음**. 종목코드가 아니라 **내부 stock id** |
| 관심테마 | GET | `/theme/v2/watchthemes` 등 | 로그인 | 배치 비사용 |

프론트 출처: `/assets/theme-factor-*.js` (`fetchAllThemes`, `fetchThemeStocks`, `fetchStockThemes`, …).

### 3.3 필드 매핑

| stocke | AlphaSquare |
|--------|-------------|
| `theme_id` / `tag_key` | `theme.id` (int) → `alphasquare_theme_{id}_{slug}` |
| `theme_name` | `theme.name` |
| `stock_code` | `code` → `zfill(6)` · `country_code=="KR"` / 국내만 필터 권장 |
| `stock_name` | `ko_name` 또는 `cname` |
| 내부 stock id (사유용) | `id` (테마 stocks 응답) |
| 테마 설명 / KEY POINT | `theme.description` (상세 API) |
| 편입 사유 | `themes-for-stock[].reason` |
| 등락/순위 (선택) | detail/leaderboard `stats.returns`, `stats.rank` |

참고: 테마 상세의 `stocks[].description`은 **기업 소개**이지 편입 사유가 아님.

### 3.4 호출량 · sleep

| 단계 | 호출 수 | 예상 wall time |
|------|---------|----------------|
| all-themes 1회 | 1 | < 2s |
| 테마별 stocks (전수) | **454** | sleep 0.3s ≈ **2.3분** / 0.5s ≈ **3.8분** |
| (선택) 상세로 설명 보강 | ≤454 | 필요 시에만. stocks만으로도 매핑 가능 |
| (선택) 편입 사유 | 고유 stock id 수(수천 추정) | v1은 **옵션**. 기본은 A(테마→종목)만 |

키움(~3초/건·테마 200+)보다 **훨씬 가벼움**. 기본 sleep **0.3~0.5s**.

샘플(테마 20개): unique KR 코드 243 / 6.4초.

---

## 4. 수집 설계

### 4.1 권장 수집 순서 (실측 반영)

```text
1) GET /theme/v2/all-themes          → 테마 454개 (+ 카테고리·description·stock_count)
2) GET /theme/v2/themes/{id}/stocks  × N → code/id 편입 (KR만)
3) (선택) GET /theme/v2/themes/{id}  → description/KEY POINT·stats 보강
4) (선택) GET /theme/v3/themes-for-stock?stock_id= → reason 보강
5) theme_map_store._store_alphasquare_theme_edges()
```

동기 래퍼: `crawl_alphasquare_theme_snapshot_sync(limit=...)`  
비동기: 배치/수동 refresh에서 호출.  
`stock_id`↔`code` 맵은 2단계에서 같이 쌓아 4단계(사유)에 재사용.

### 4.2 정규화 규칙

| 필드 | 규칙 |
|------|------|
| `stock_code` | 숫자만 추출 후 `zfill(6)` |
| `tag_key` | `alphasquare_theme_{theme_id}_{slug(name)}` |
| `tag_type` | `theme` |
| `source` | `alphasquare_theme` |
| 당일 엣지 | 기존 키움과 동일 — 같은 `source`·당일 `observed_at` 엣지 삭제 후 upsert |
| 표시 머지 | `_SOURCE_RANK["alphasquare_theme"] = 0` (네이버·키움과 동급) |

### 4.3 `meta_json` 권장 스키마

**Tag meta**

```json
{
  "alphasquare_theme_id": "…",
  "description": "…",
  "key_point": "…",
  "stock_count": 40,
  "change_rate": 2.1,
  "collected_via": "internal_api"
}
```

**Edge meta**

```json
{
  "alphasquare_theme_id": "…",
  "reason": "편입 사유 텍스트",
  "role_hint": "leader|member|null"
}
```

### 4.4 유니버스 전략 (제품 의도)

요청 의도: **“알파스퀘어에 있는 종목의 테마정보를 기반으로 한 번 더 수집”**.

| 옵션 | 동작 | 권장 |
|------|------|------|
| **A. 테마→종목 전수** | 알파스퀘어 테마 카탈로그 전체 walk | **1차 권장** (키움/네이버와 대칭, 커버리지↑) |
| **B. 종목→테마** | stocke 보유/테마맵 종목만 알파스퀘어 종목 API로 조회 | 호출 수↓, API가 stock→themes 제공 시 보조 |
| **C. 교집합** | 기존 `theme_tag_edges` 종목코드만 대상 | 보강 전용, 신규 테마 발견 약함 |

1차는 **A**. Phase 0에서 stock→themes API가 싸고 안정적이면 **A+B 하이브리드**(전수 테마 + 미매핑 종목 보강) 검토.

---

## 5. 시스템 연동

### 5.1 모듈 배치

| 파일 | 역할 |
|------|------|
| `utils/theme_alphasquare_crawler.py` | HTTP 클라이언트, list/stocks/snapshot |
| `utils/theme_map_store.py` | `SOURCE_ALPHASQUARE`, `_store_alphasquare_theme_edges`, `refresh_*` 플래그 |
| `scripts/theme_mart_batch.py` | `--no-alphasquare` / `--alphasquare-only` |
| `core/config.py` + `env_example.txt` | base URL, timeout, sleep, auth secret |
| `core/main.py` | `POST /theme-map/refresh?include_alphasquare=` |
| `utils/batch_scheduler_status.py` | 배치 상태 키 (기존 theme_mart에 스테이지 포함 또는 별도) |
| `tests/test_alphasquare_theme.py` | 파서·정규화·store mock 단위 테스트 |

### 5.2 배치 파이프라인 (목표)

```text
theme_mart_batch
  Stage A  Naver
  Stage B  News (optional)
  Stage C  Kiwoom
  Stage D  AlphaSquare   ← NEW
  Stage E  theme_score (optional)
```

기본 스케줄: 기존 테마마트와 동일 (**Daily 18:00**).  
키움 rate limit와 겹치면 AlphaSquare를 키움 이후·sleep 분리.

### 5.3 설정 키 (안)

| Env | 기본 | 설명 |
|-----|------|------|
| `ALPHASQUARE_ENABLED` | `true` | 배치 포함 여부 |
| `ALPHASQUARE_BASE_URL` | `https://api.alphasquare.co.kr` | API origin |
| `ALPHASQUARE_TIMEOUT_SEC` | `20` | 요청 타임아웃 |
| `ALPHASQUARE_SLEEP_SEC` | `0.35` | 테마당 간격 |
| `ALPHASQUARE_FETCH_REASONS` | `false` | v1 기본 OFF · stock→themes 사유 보강 |
| `ALPHASQUARE_USER_AGENT` | 브라우저 UA | 차단 완화용 |

공개 테마 조회에 쿠키/토큰 **불필요**. 넣지 않는다.

### 5.4 API (앱 내부)

기존 refresh 확장:

```
POST /theme-map/refresh?include_naver=1&include_kiwoom=1&include_alphasquare=1
```

응답 요약에 `alphasquare_ok`, `alphasquare_themes`, `alphasquare_edges` 추가.

### 5.5 UI

| 화면 | 변경 |
|------|------|
| 테마맵 | 소스 필터/배지에 `alphasquare` 표시 · 상세에 reason/KEY POINT |
| 대시보드 스크리너 | 칩 머지에 자동 포함 (별도 컬럼 불필요) |
| 배치 현황 | AlphaSquare 스테이지 ok/실패·마지막 시각 |

---

## 6. 성공 지표

| 지표 | 목표 (1차) |
|------|------------|
| 일 배치 성공률 | ≥ 95% (실패 시 네이버/키움만으로 degrade) |
| 신규 커버리지 | 기존 theme-universe 대비 **테마 미매핑 종목 감소** (Phase 0 베이스라인 대비 %) |
| 교차 일치 | 동일 종목에서 네이버∪키움∪알파스퀘어 테마명 교집합이 의미 있게 존재 (리포트) |
| 운영 비용 | 전수 수집 wall time ≤ 키움 스테이지와 유사 또는 그 이하 |
| 장애 격리 | AlphaSquare 실패가 Naver/Kiwoom 커밋을 롤백하지 않음 |

---

## 7. 구현 Phase

| Phase | 내용 | Done 정의 |
|-------|------|-----------|
| **0** | API 스파이크·필드 매핑·운영 기준 | **완료** (§3) |
| **1** | crawler + store + unit test | **완료** |
| **2** | `theme_mart_batch` 플래그·스케줄·`/theme-map/refresh` | **완료** |
| **3** | 테마맵 소스 배지 · reason/KEY POINT | **완료** — N/K/AS 칩, 소스 필터, 사유·KEY POINT, 배치현황 AS 시각 |
| **4** | 네이버/키움 교차 리포트 · `FETCH_REASONS` | **완료** — `GET /theme-map/source-cross`, `--cross-report`, `--fetch-reasons` |

Phase 3–4 사용:
```bash
# 교차 리포트만
python scripts/theme_mart_batch.py --cross-report-only --no-telegram

# 수집 + 교차 리포트 (텔레그램에 소스 교차 섹션)
python scripts/theme_mart_batch.py --top-n 0 --no-news --cross-report

# 편입사유까지 (호출 수↑)
python scripts/theme_mart_batch.py --alphasquare-only --fetch-reasons
```
UI: `/theme-map` 소스 필터 · 교차 커버리지 카드 · 종목/태그 상세에 사유  
API: `GET /theme-map/source-cross` · `GET /theme-map/tags?source=alphasquare_theme`  
Env: `ALPHASQUARE_FETCH_REASONS=false` (기본) / CLI `--fetch-reasons` 로 켠다.

---

## 8. 수용 기준 (Acceptance)

1. `theme_mart_batch` (또는 `--alphasquare-only`) 실행 시 `theme_tag_edges.source = 'alphasquare_theme'` 당일 스냅샷이 생긴다.  
2. `get_latest_map_by_codes([code])` 결과에 알파스퀘어 테마명이 네이버/키움과 함께 나타난다.  
3. AlphaSquare API 장애 시에도 네이버·키움 스테이지 결과는 유지된다.  
4. 시크릿(쿠키/토큰)은 env만 사용하고 저장소에 커밋되지 않는다.  
5. `tests/test_alphasquare_theme.py`가 코드 정규화·tag_key·당일 replace 동작을 커버한다.

---

## 9. 오픈 질문

| # | 질문 | 상태 |
|---|------|------|
| 1 | base / 버전 | **닫힘** — `api.alphasquare.co.kr` · `/theme/v2`·`/theme/v3` |
| 2 | 인증 | **닫힘** — 공개 테마 무인증. watchthemes만 로그인 |
| 3 | 전수 규모 | **닫힘** — 테마 454 · membership sum ≈9328 · stocks≈2~4분 |
| 4 | stock→themes | **닫힘** — `/theme/v3/themes-for-stock?stock_id=` (내부 id) |
| 5 | 편입 사유 위치 | **닫힘** — stocks 목록에는 없음. themes-for-stock의 `reason` |
| 6 | 국내만 필터 | **권장 YES** — `country_code=="KR"` + `code` 6자리 |
| 7 | v1에서 사유 수집 ON? | **닫힘** — 기본 OFF (`ALPHASQUARE_FETCH_REASONS` / `--fetch-reasons`) |

---

## 10. 관련 문서 · 참고 UI

- `docs/THEME_STOCK_PIPELINE.md` — 소스 테이블·스키마·머지 규칙  
- `docs/PRD_JONGGA_BETTING.md` — 테마 맵 소비(최강테마)  
- 알파스퀘어 테마 UI: https://alphasquare.co.kr/home/theme-factor  
- 헬프: 테마 편입 사유 / KEY POINT (alphasquare.oopy.io 가이드)

---

## 부록 A. 기존 소스와의 위치

```text
                    ┌─────────────┐
                    │ theme_tags  │
                    │ theme_tag_  │
                    │ edges       │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
     naver_theme     kiwoom_theme   alphasquare_theme  ← NEW
           │               │               │
           └───────────────┴───────────────┘
                           ▼
              get_latest_map_by_codes()
                           ▼
         screener / jongga / theme_map / news universe
```

## 부록 B. CLI 스케치

```bash
# 알파스퀘어만
python scripts/theme_mart_batch.py --alphasquare-only --top-n 0

# 기존 + 알파스퀘어 (기본 ON 예정)
python scripts/theme_mart_batch.py --top-n 0 --no-news

# 알파스퀘어 제외
python scripts/theme_mart_batch.py --no-alphasquare
```
