# 테마·키워드 ↔ 종목 매핑 파이프라인 — 개발 기획서

> **1차 목적**: 종목을 지정하면 **어떤 테마·키워드에 속하는지** 데이터화하고, 반대로 키워드를 지정하면 **어떤 종목들이 연결되는지** 입체적으로 볼 수 있게 한다.  
> **실사용 다음 목표**: 스크리너 후보 표에 **종목별 테마·키워드 컬럼** (§6.2, Phase 1.5).  
> **자동매매 판정·스코어 THRESHOLD**: 보류. 표시(enrichment)와 분리.  
> **LLM**: v2 이후 신중 검토 (API 비용은 §8).  
> **작성 기준**: 2026-07-09, stocke (`theme_mart_batch` 1h, `stock_news_daily_batch` 16:30, `theme_map`, `screener/candidates`, dashboard).

---

## 1. 한 줄 요약

| 질문 | 답 |
|------|-----|
| 이게 뭔가? | **종목 ↔ 테마/키워드 이종 그래프**(bipartite graph)를 매일 갱신하는 내부 데이터 레이어 |
| v1에서 뭘 만드나? | 수집 배치 + SQLite + **양방향 조회 API** + **시각화** + **스크리너 후보에 테마/키워드 표시** |
| 스크리너 연동? | **매수 신호 생성 전, 후보 테이블에 종목별 테마·키워드 컬럼** (관찰/설명 우선, 게이트는 아님) |
| 스코어링? | **v1 필수 아님**. 나중에 “오늘의 강한 테마” 랭킹용으로 **선택** 추가 |
| 키워드 추출? | **KeyBERT**(한국어 SBERT) + Kiwi 후보 + 룰 fallback · `config/keyword_rules/*` |
| 자동매매? | **매매 판정 연동은 보류**. 스크리너 **표시**는 Phase 1.5에서 우선 |
| LLM? | **v1 없음**. 요약·태그 보강은 v2 (비용은 §8) |

---

## 2. 용어 정리 (말하고 싶은 것 ↔ 추천 용어)

말씀하신 요구는 업계에서 보통 아래처럼 부른다. 이 문서에서는 **한글(영문)** 을 같이 쓴다.

| 말하고 싶은 것 | 추천 용어 | 설명 |
|----------------|-----------|------|
| “이 종목은 뭐 테마야?” | **종목 → 태그 조회** (stock → tags) | 한 종목이 여러 테마/키워드에 **동시에** 속할 수 있음 (N:M) |
| “이 키워드면 어떤 종목?” | **태그 → 종목 조회** (tag → stocks) | 한 테마에 종목 다수, 종목은 여러 테마에 중복 편입 가능 |
| 테마·이슈 이름 전체 | **태그(tag)** 또는 **키워드(keyword)** | 네이버 “2차전지”, 뉴스 “HBM”, 업종 “반도체” 모두 태그 후보 |
| 종목이 태그에 속한다는 사실 | **매핑(mapping)** · **편입(edge)** | `(stock_code, tag_id, source, observed_at)` 한 줄 |
| 태그끼리 계층 | **택소노미(taxonomy)** (선택) | 예: `반도체` > `HBM` > `장비` — v1은 평면 리스트로 시작해도 됨 |
| 오늘 시장에서 이 테마가 얼마나 붐빔 | **테마 스냅샷 / 모멘텀** (선택) | 등락률·거래대금·뉴스 건수 — **스코어는 여기서 파생**, v1 필수 아님 |

**피하고 싶은 혼동**

- **“테마주 검색”** alone → 매매 후수 종목 찾기 느낌. 지금 목표는 **지식 그래프 + 탐색 UI**에 가깝다.
- **“분류(classification)”** alone → 보통 종목당 **하나**의 라벨. 실제는 **복수 태그**가 맞다.
- **“섹터/업종”** → KRX·네이버 업종은 **공식 분류**. “조선·AI·2차전지” 같은 **이슈 테마**와는 별도 축으로 저장하는 게 좋다.

**문서 내 통일 명칭**: DB·API에서는 `tag` / `stock_tag_edge` / `tag_type` (`theme` | `sector` | `news_keyword` | `manual`).

---

## 3. 제품 관점 — 사용자가 보고 싶은 화면

### 3.1 종목 중심 (Stock-centric)

```
입력: 삼성전자(005930)
출력:
  - 네이버 테마: 반도체, AI반도체, …
  - 업종: 전기·전자
  - 최근 24h 뉴스 키워드: HBM, 파운드리, …
  - 동시에 많이 붙는 다른 종목 (공편 태그 기준)
```

### 3.2 태그 중심 (Tag-centric)

```
입력: "2차전지" (또는 "HBM")
출력:
  - 편입 종목 리스트 (네이버 테마 공식 + 뉴스 co-occurrence 보조)
  - 오늘 등락·거래대금 (키움/시총 크롤러)
  - 관련 기사 제목·URL
  - (선택) 서브태그: 양극재, LFP, …
```

### 3.3 입체적으로 (시각화)

v1에서 현실적인 UI (무거운 그래프 엔진 없이):

| 뷰 | 설명 |
|----|------|
| **태그 클라우드 + 종목 테이블** | 태그 클릭 → 우측 종목表. 종목 클릭 → 소속 태그 칩 |
| **공편 행렬** | 상위 N태그 × 상위 M종목 히트맵 (편입 여부·등락률 색) |
| **타임라인** | `observed_at` 기준 “이 종목에 이 태그가 언제 붙었/빠졌는지” |
| **(v2) 포스 그래프** | D3/vis.js — 태그·종목 노드, 클릭 필터 (노드 수 제한 필수) |

배치 위치 후보: `/analysis` 확장 탭, 또는 `/static/theme-map.html` 신규.

### 3.3b 스크리너 후보 (Screener enrichment) — 다음 우선

대시보드 **스크리너** 표의 거래대금·조건식 후보 옆에 바로:

| 컬럼 | 내용 |
|------|------|
| 테마 | 네이버 테마. 상위 2~3개 칩 |
| 키워드 | 당일/최근 뉴스 키워드. 상위 2~3개 칩 |

클릭 시 theme-map 또는 `/stocks/{code}/tags`로 이동 (Phase 2 딥링크).  
상세는 **§6.2**, 일정은 **Phase 1.5**.

### 3.4 오늘의 키워드 (Daily Keywords) — 추가

실시간 스트리밍이 아니어도, 장중/장후 기준으로 **오늘 많이 등장한 키워드**를 요약해 볼 수 있다.

```
입력: 당일 수집된 뉴스 제목/태그
출력:
  - 키워드 TOP N (예: HBM, 조선, 전력기기)
  - 전일 대비 증감(신규/급증/감소)
  - 키워드별 연결 종목 수, 대표 종목 3개
  - 키워드 근거 기사 수/링크
```

권장 UI:

| 뷰 | 설명 |
|----|------|
| **오늘의 키워드 카드** | 키워드명 + 건수 + 증감 배지 (`NEW`, `▲`, `▼`) |
| **키워드 상세 패널** | 연결 종목, 관련 기사 제목 5개, 최근 3일 추이 |
| **키워드↔종목 교차표** | 키워드 클릭 시 종목 테이블 동기 필터 |

---

## 4. 왜 파이프라인인가 (API가 없는 이유)

| 소스 | 주는 것 | 한계 |
|------|---------|------|
| 네이버 금융 테마 | 테마 ↔ 종목 (공식 편입) | 테마명·편입이 HTS와 다를 수 있음, HTML 파싱 |
| 키움 REST 테마 (`ka90001`/`ka90002`) | HTS 계열 테마 ↔ 종목 + 등락/기간수익 | 호출 한도(장후 배치, `source=kiwoom_theme`) |
| 네이버 업종 | 종목 ↔ 업종 | “이슈 테마”와 다름 |
| 네이버 뉴스 API | 종목·키워드 ↔ 기사 | **종목-키워드 직접 매핑 API 아님** → 제목에서 추출·공출현 |
| 키움 시세 | 시세·거래대금 | (테마 메타는 위 REST 테마 TR 사용) |
| 토스 등 | 앱 중심 | v1 미사용 |

→ **여러 소스를 같은 `tag` / `stock_tag_edge` 스키마로 합치는 ETL**이 핵심이다.

---

## 5. 데이터 모델 (v1 핵심)

### 5.1 엔티티

```text
tags
  id, tag_key, name_ko, tag_type, parent_id (nullable), created_at

stocks
  stock_code (PK), stock_name, updated_at   # 기존 종목 마스터와 JOIN

stock_tag_edges
  id, stock_code, tag_id, source, role, weight, observed_at, meta_json
  UNIQUE(stock_code, tag_id, source, observed_at)  -- 또는 스냅샷 단위

tag_snapshots (선택 — “오늘 이 태그 시장 수치”)
  id, tag_id, collected_at, change_pct, news_count_24h, stock_count, ...

tag_articles
  tag_id, title, url, published_at, stock_code (nullable)

keyword_daily_stats (오늘의 키워드 집계)
  keyword, biz_date, mention_count, stock_count, delta_vs_prev, trend_label, updated_at
```

### 5.2 `source` (근거 출처 — 신뢰도 판단용)

| source | 의미 | weight 예시 |
|--------|------|-------------|
| `naver_theme` | 네이버 테마 페이지 공식 편입 | 1.0 |
| `naver_sector` | 네이버 업종 | 0.9 |
| `news_title` | 뉴스 제목 키워드 매칭 | 0.3~0.6 |
| `news_cooccur` | 같은 기사/같은 날 복수 종목 | 0.4 |
| `manual` | 사용자 수동 태그 | 1.0 |

**v1에서는 `weight`·스코어를 매매 THRESHOLD에 쓰지 않는다.** 조회·필터·시각화용 메타만.

### 5.3 `role` (종목이 태그 안에서의 역할 — 선택)

| role | 의미 |
|------|------|
| `leader` | 대장주 후보 (네이버 테마 대표종목 등) |
| `member` | 일반 편입 |
| `peer` | 뉴스 공출현만으로 연결 |

### 5.4 양방향 쿼리 예시

```sql
-- 종목 → 태그
SELECT t.name_ko, t.tag_type, e.source, e.weight, e.observed_at
FROM stock_tag_edges e
JOIN tags t ON t.id = e.tag_id
WHERE e.stock_code = '005930'
ORDER BY e.observed_at DESC, e.weight DESC;

-- 태그 → 종목
SELECT s.stock_code, s.stock_name, e.role, e.source, e.weight
FROM stock_tag_edges e
JOIN stocks s ON s.stock_code = e.stock_code
WHERE e.tag_id = :tag_id
ORDER BY e.weight DESC, s.stock_name;
```

---

## 6. 파이프라인 (수집 → 정규화 → 저장)

```
[배치 theme_mart_batch.py]
  Stage A  네이버 theme.naver → tags + edges (naver_theme)
  Stage B  종목별 업종 (선택) → tags (naver_sector)
  Stage C  테마/종목별 뉴스 API → tag_articles + news 키워드 edges
  Stage D  오늘의 키워드 집계 → keyword_daily_stats
  Stage E  (선택) 당일 시세 스냅샷 → tag_snapshots
  Stage F  API /tags, /stocks/{code}/tags, /tags/{id}/stocks, /keywords/today
```

### 6.1.8 Stage C — 종목별 네이버 뉴스 검색 API ✅ (구현됨)

사용자 의도(“종목별 기사 = 네이버 뉴스 검색 API”)를 v1 설계에 맞게 확장한다.

**구현**: `scripts/stock_news_daily_batch.py` · 스케줄 `stocke-stock-news-batch` (기본 **16:30**, `install_stock_news_batch_task.ps1`)

#### 목적
- 종목별 **근거 기사**를 `tag_articles`에 저장(제목/URL/발행시각)
- 기사 제목에서 키워드를 추출해 **뉴스 키워드 태그**(= `tag_type=news_keyword`)로 연결
- 그 결과를 `keyword_daily_stats`(오늘의 키워드 TOP)에도 반영

#### (선택) 초기 부트스트랩 1회
- “전 종목 × 전체 기간” 수준으로 무리하게 돌리기보다,
  - **최근 60~90일**의 “시장 공통 키워드”를 시장 전체 쿼리로 한번 훑어 canonical 키워드 후보/동의어 규칙을 안정화한다.
- 이후엔 Stage C를 **매일 당일 범위**(또는 후처리 컷)로만 운영한다.

#### 수집 대상(가장 중요 — API 제한 대응)
- **전체 종목(네이버 금융 시총 목록 기준)** 을 대상으로 매일 수집을 지원한다.  
  - API 제한/시간 부담이 있으면 `--max-stocks-per-run`로 쪼개서 여러 번 실행할 수 있다.
- `biz_date` 기준으로 이미 수집된 종목은 자동 스킵하며(`tag_articles` 존재 확인),
  - 예외적으로 `--force`로 재수집 가능.

#### Naver API 쿼리 형태
- `query`에는 종목명과 종목코드를 함께 넣어 동명이슈를 줄인다.
  - 예: `query = f"{stock_name} {stock_code}"` (또는 `${stock_code} OR ${stock_name}`)
- 요청 시 `display`는 10~20개 수준으로 제한(제목 기반 키워드 추출에 충분한 범위).
- `sort=date`로 정렬한 뒤, **후처리에서 published_at이 당일 범위를 벗어나면 폐기**한다.
  - (API가 날짜 필터를 안정적으로 제공하지 않는다는 전제)

#### 중복 제거/저장 전략
- `tag_articles`는 최소한 `(url)` 또는 `(title, published_at)` 기준으로 중복 저장을 막는다.
- 같은 기사에서 키워드가 여러 번 추출되더라도:
  - `keyword_daily_stats`는 “오늘(biz_date) 기준 집계”로 정규화
  - 종목-키워드 연결은 `theme_tag_edges`/`news_keyword` 계열 edge로 **스냅샷 dedupe**(source+observed_at 기준)한다.

#### 스케줄 권장(“장 끝나고”)
- **장 마감 직후 30~90분 내** 1일 1회 수행:
  - 왜: 당일 기사 품질이 안정되고, 장중 키움 스레드/네트워크 부하와 충돌을 줄일 수 있음
- 장중에는 `theme_mart_batch`(1시간 단위)가 키워드/관찰용을 담당하고,
  - Stage C는 “관찰/근거” 레이어를 매일 보강한다.

#### 품질/운영 지표
- `오늘의 종목별 기사 커버리지`: target 종목 중 최소 3개 기사 이상 수집 비율
- `스크리너 top50 중 근거 키워드 표시율`: (테마/키워드 칩이 뜨는 종목 비율)
- `429/타임아웃 발생률`: 회피가 필요한지 판단하는 운영 신호
- **배치 진행률**: `GET /batch-status/stock-news-progress` · 대시보드 배치 카드 (`logs/_stock_news_progress.json`)

#### 저장 테이블 (Stage C 산출물)
| 테이블 | 내용 |
|--------|------|
| `tag_articles` | 기사 title / url / published_at |
| `theme_tags` (`tag_type=news_keyword`) | 추출 키워드 태그 |
| `theme_tag_edges` (`source=news_title`) | 종목 ↔ 키워드 (weight=`mention_count` 또는 KeyBERT `score` 기반) |
| `tag_article_keyword_edges` | 기사 ↔ 키워드 (제목당 top-N) |
| `keyword_daily_stats` | 당일 키워드 TOP 집계·전일 대비 증감 |

---

**스코어링 Stage는 v1에서 생략 가능.** 나중에 추가할 때만:

```text
theme_score = f(테마등락, 동반상승비율, 거래대금순위, 뉴스건수)  -- THRESHOLD는 미정
```

### 6.1 키워드 추출 (구현됨 — KeyBERT + 룰)

LLM 없이 운영 가능한 수준으로, `뉴스 제목` 중심 추출을 **KeyBERT 임베딩 유사도**로 고도화했다.  
규칙 파일(`config/keyword_rules/*`)·동의어·stopword는 그대로 후처리에 사용한다.

#### 6.1.1 처리 파이프라인 (3단계 fallback)

```text
원문 제목(들)
  → 정규화(normalize) + 종목코드 제거
  → 후보 생성: Kiwi 명사 + 토큰 n-gram + whitelist 구문
  → [1] KeyBERT: 문서 임베딩 ↔ 후보 임베딩 코사인 유사도 + MMR
  → [2] (torch 실패 시) Kiwi 명사 후보 빈도
  → [3] (최종 fallback) 공백 토큰 빈도
  → 동의어 canonicalize · stopword/whitelist 필터
  → 저장(keyword_daily_stats, theme_tag_edges[source=news_title], tag_article_keyword_edges)
```

**진입점**: `utils/theme_keyword_rules.extract_keywords()` — `stock_news_daily_batch`, `theme_map_store` 공통 사용.

#### 6.1.1b KeyBERT 상세 (2026-07-09 도입)

| 항목 | 값 |
|------|-----|
| 모듈 | `utils/theme_keyword_keybert.py` |
| 임베딩 모델 | `jhgan/ko-sroberta-multitask` (env: `KEYBERT_MODEL`) |
| 후보 | Kiwi 명사(`kiwipiepy`) + 토큰 1~2gram + `keyword_whitelist.txt` |
| 랭킹 | KeyBERT cosine similarity + MMR(`KEYBERT_USE_MMR`, `KEYBERT_DIVERSITY`) |
| 출력 필드 | `keyword`, `mention_count`(기사/문서 적중 수), `score`, `score_sum`(KeyBERT 성공 시) |
| 의존성 | `keybert`, `sentence-transformers`, `kiwipiepy`, `torch` (`requirements.txt`) |
| Windows | Visual C++ Redistributable 2015–2022 (x64) — torch DLL 로드용 |

환경변수 (`core/config.py`):

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `KEYWORD_USE_KEYBERT` | `true` | KeyBERT 사용 |
| `KEYBERT_MODEL` | `jhgan/ko-sroberta-multitask` | SBERT 모델 |
| `KEYBERT_USE_MMR` | `true` | 유사 키워드 중복 완화 |
| `KEYBERT_DIVERSITY` | `0.5` | MMR diversity |
| `KEYBERT_USE_KIWI` | `true` | 명사 후보 추출 |

첫 실행 시 Hugging Face에서 SBERT 가중치 다운로드(수 분). 이후 모델은 프로세스 내 싱글톤 캐시.

#### 6.1.2 정규화 규칙 (normalize)

| 항목 | 규칙 |
|------|------|
| 공백/구분자 | 연속 공백 1칸, `·`, `/`, `-`, `_`는 토큰 경계로 처리 |
| 대소문자 | 영문은 소문자 통일 (`HBM`은 예외 whitelist로 원형 유지) |
| 숫자/기호 | `%`, `조원`, `만대` 같은 수치 단위는 분리 보존 |
| 괄호/태그 | `[특징주]`, `(종합)`, `(속보)` 등 머리말/꼬리말 제거 |
| 종목코드 | `005930`, `A005930` 형태는 종목코드로 별도 추출하고 키워드 후보에서는 제외 |

#### 6.1.3 불용어(stopwords) 기준

아래는 기본 stopwords 묶음이며, 파일로 분리해 운영 중 계속 보정한다.

- **기사 형식어**: `속보`, `단독`, `종합`, `기자`, `인터뷰`, `영상`, `포토`
- **시장 일반어**: `상승`, `하락`, `급등`, `급락`, `강세`, `약세`, `마감`, `장중`
- **기능어/조사**: `관련`, `대해`, `통해`, `위해`, `전망`, `가능성`, `확대`
- **너무 일반적인 경제어**: `실적`, `매출`, `영업이익` (단, 필요 시 화이트리스트로 복구)

**운영 룰**:
- 1글자 토큰 제거
- 숫자만 있는 토큰 제거
- 당일 전체 기사의 70% 이상에 등장하는 토큰은 임시 stopword로 다운그레이드

#### 6.1.4 동의어/표기 통합 (synonym map)

동일 개념의 표기를 하나로 묶어야 “오늘의 키워드” 품질이 올라간다.

| 대표 키워드(canonical) | 매핑 예시(alias) |
|------------------------|------------------|
| `2차전지` | `이차전지`, `배터리`, `2차 전지` |
| `반도체` | `semiconductor`, `칩` |
| `HBM` | `고대역폭메모리`, `고대역폭 메모리` |
| `원전` | `원자력`, `소형원전`, `SMR` |
| `조선` | `조선업`, `선박` |
| `전력기기` | `전력 설비`, `변압기`, `송배전` |

저장 규칙:
- 원문 토큰(`raw_keyword`)과 대표 키워드(`keyword`)를 모두 저장
- UI/집계는 대표 키워드 기준, 상세 drill-down에서 원문 토큰 확인

#### 6.1.5 키워드 점수화

**KeyBERT 경로** (기본):

```text
rank_key = doc_hits × max_cosine_score × score_sum
edge_weight = mention_count  (배치 theme_tag_edges.weight)
ui_score    = score (0~1, keywords/today 정렬 보조)
```

**규칙 fallback 경로**:

```text
keyword_score = mention_count
              + 0.5 * unique_stock_count
              + source_weight_bonus
```

- `mention_count`: 해당 키워드가 등장한 기사(또는 문서) 수
- `unique_stock_count`: 해당 키워드와 연결된 고유 종목 수 (`keyword_daily_stats`)
- `score`: KeyBERT 코사인 유사도 (의미적 관련도)
- `source_weight_bonus`: `naver_theme`와 동시 매핑되면 +2 같은 보너스 (향후)

v1에서는 이 점수를 **자동매매에 사용하지 않고**, `keywords/today`·edge weight·스크리너 표시용으로만 사용.

#### 6.1.6 품질 보호 장치

- 최소 기사 수: `mention_count >= 3` 미만은 기본 숨김(옵션으로 표시)
- 최대 상위 노출: TOP 20만 표시
- 변동성 태그: 전일 대비 2배 이상이면 `surge`, 신규면 `new`
- 감사 로그: 일자별 `dropped_by_stopword`, `merged_by_synonym` 집계 저장

#### 6.1.7 설정 파일 제안

```text
config/keyword_rules/
  stopwords_ko.txt
  stopwords_market.txt
  synonyms.yml
  keyword_whitelist.txt
```

운영자가 파일만 수정해도 배치 재시작 후 바로 반영되도록 설계한다.

### 스케줄 (미니PC)

| 작업 | 시각 | 부하 | 비고 |
|------|------|------|------|
| `theme_mart_batch` (키워드 중심) | **장중 1시간** (예: 08:30~15:30) | 1~3분 | 이슈 키워드 변동 반영 |
| 테마 구조 스냅샷 | 장전 1회 + (선택) 장중 1~2회 | 2~5분 | 네이버 테마 편입 목록 |
| `stock_news_daily_batch` (종목별 근거 기사/키워드) | **매일 16:30** (장 마감 후) | (분할 실행) | `stocke-stock-news-batch` · `--max-stocks-per-run` 분할 · 429 백오프 |
| 스크리너 표시용 조인 | **런타임**(API 요청 시) | DB 조회만 | 배치 결과는 `theme_tag_edges` |

`fundamental_mart_batch` / 키움 대량 조회와 **10분 이상 간격**.

---

## 6.2 스크리너 후보 × 테마/키워드 (핵심 제품)

### 제품 의도

스크리너 후보(거래대금순 + 조건식) 표에 **종목마다 테마·키워드를 같이 보여** “이 종목이 왜 떠있는지”를 바로 읽게 한다.  
→ **매수 판정 게이트가 아니라 observation/UI enrichment**.

### 왜 배치에 넣어야 하나

- 스크리너 API는 주기적으로 돌고, 키움 호출이 이미 있다.
- 테마/키워드를 **스크리너 중에 실시간 크롤**하면 API·차단 리스크가 커진다.
- 따라서 배치가 `stock_code → [tags]` 를 미리 쌓고, 스크리너는 **조인만** 한다.  
  (`fundamental_mart`의 PER/PBR을 `/screener/candidates`에 붙이는 패턴과 동일)

### 데이터 흐름

```text
[배치 theme_mart_batch — 1시간]
  네이버 테마 편입 + (선택) 뉴스 제목 키워드
        ↓
  theme_tags / theme_tag_edges / keyword_daily_stats
        ↓
[런타임 GET /screener/candidates]
  volume_rank + condition merge
        ↓
  get_tags_map_by_codes(codes)   ← fundamental_map과 병렬
        ↓
  후보 행에 themes[], keywords[] (또는 표시용 문자열) 첨부
        ↓
[대시보드 스크리너 표]
  새 컬럼: 테마 | 키워드
```

### 칸/필드 설계 (스크리너 응답)

| 필드 | 타입 | 예시 |  नियम |
|------|------|------|------|
| `themes` | string[] | `["2차전지","AI반도체"]` | `tag_type=theme`, source 우선순위 `naver_theme` |
| `keywords` | string[] | `["HBM","전력기기"]` | `tag_type=news_keyword` (또는 당일 키워드) |
| `theme_text` | string | `2차전지, AI반도체` | UI 한 줄 표시용 (상위 2~3개) |
| `keyword_text` | string | `HBM · 전력` | UI 한 줄 표시용 (상위 2~3개) |
| `tag_freshness` | ISO time | edge `observed_at` max | stale이면 UI에 `갱신 n시간 전` |

**표시 상한 (부하·가독성)**  
- 테마 최대 3, 키워드 최대 3  
- 정렬: `weight DESC`, 동점이면 `naver_theme` > `news_title` > 기타

### 개발 방법 (코드 터치포인트)

| 단계 | 파일 | 작업 |
|------|------|------|
| 1 | `utils/theme_map_store.py` | `get_latest_map_by_codes(codes)` — stock_code → {themes, keywords} (펀더멘털 `get_latest_map_by_codes` 동형) |
| 2 | `core/main.py` `/screener/candidates` | 후보 수집 후 codes로 태그 맵 조인, 각 item에 필드 부착 |
| 3 | `managers/auto_trade_scanner.py` (선택) | 스캔 로그에 `theme_text` 한 줄 덧붙이기 (게이트 미사용) |
| 4 | `static/dashboard.js` `loadScreener` | 테이블 thead에 `테마`/`키워드` 컬럼, 칩 UI |
| 5 | `scripts/theme_mart_batch.py` | 이미 있는 edge 적재를 **스크리너 커버리지 충분**하도록 top_n·뉴스 옵션 유지 (1h 스케줄) |

### SQL/조회 스케치

```sql
-- 후보 종목 codes IN (...)
SELECT e.stock_code, t.name_ko, t.tag_type, e.source, e.weight, e.observed_at
FROM theme_tag_edges e
JOIN theme_tags t ON t.id = e.tag_id
WHERE e.stock_code IN (:codes)
  AND e.observed_at >= :since   -- 예: 당일 또는 최근 24h
ORDER BY e.weight DESC, e.observed_at DESC;
```

앱 레이어에서 stock별로 cut top-N 후 API 응답에 넣는다.

### 품질·운영 주의

| 이슈 | 대응 |
|------|------|
| 배치 실패로 열 비어 있음 | 스크리너는 정상 동작, 컬럼은 `-` / `미갱신` |
| edge가 너무 많음 | top-N cut + source filter |
| 시간당 배치 vs 스크리너 2분 | 스크리너는 DB만 읽음 → 키움 부하 증가 없음 |
| fallback/샘플 테마 | 실데이터 안정화 후 표시, 샘플이면 UI에 `demo` 뱃지 |

### 비목적 (이 단계에서 하지 않음)

- 테마/키워드를 이유로 매수 신호 **하드 컷**
- `min_theme_score` 같은 THRESHOLD로 후보 축소  
  → §13에서 데이터 쌓인 뒤 실험

---

## 7. API 초안 (자동매매 없이)

```
GET  /tags                          # 태그 목록 (type 필터)
GET  /tags/{id}                     # 태그 상세 + 스냅샷
GET  /tags/{id}/stocks              # 태그 → 종목
GET  /stocks/{code}/tags            # 종목 → 태그
GET  /tags/graph?tag_ids=1,2,3      # 시각화용 부분 그래프 (v1)
GET  /keywords/today                # 오늘의 키워드 TOP N
GET  /keywords/{keyword}/stocks     # 키워드 → 연결 종목
GET  /keywords/{keyword}/articles   # 키워드 근거 기사
POST /themes/refresh                # 배치 수동 트리거
                                       # (또는 POST /theme-map/refresh)

# 기존 스크리너 — Phase 1.5에서 enrichment
GET  /screener/candidates           # items[].themes / keywords / theme_text / keyword_text
```

---

## 8. LLM 도입 검토 (v2 — 신중 검토)

v1 **미사용**. 비용 부담은 크지 않으나, **환각·유지보수·룰/뉴스만으로 충분한지** 보고 결정.

### 8.1 1회(테마 20개 요약) 토큰·원가

| 항목 | 추정 |
|------|------|
| 1테마 | ~1,000 토큰 |
| 20테마 1회 | ~2만~2.7만 토큰 |
| GPT-4o mini 1회 | **약 6~8원** |
| 장전 1회/일 × 22일 | **약 150~200원/월** |

LLM이 유용한 경우: 태그 **동의어 정규화** (“2차전지”=“배터리”), 기사에서 **신규 키워드 제안** — 매매 신호가 아님.

---

## 9. 구현 방식

Python 배치 + SQLite + FastAPI (`fundamental_mart_batch` 패턴). 미니PC 단독.

```text
scripts/theme_mart_batch.py              # 1h 스케줄 (키워드 중심)
scripts/stock_news_daily_batch.py       # 일 단위 종목별 기사/키워드(Stage C) ✅
scripts/install_theme_batch_task.ps1
scripts/install_stock_news_batch_task.ps1  # stocke-stock-news-batch 16:30
utils/theme_naver_crawler.py
utils/theme_map_store.py                 # CRUD + get_latest_map_by_codes
utils/theme_keyword_rules.py             # extract_keywords() 진입점 + 규칙 fallback
utils/theme_keyword_keybert.py           # KeyBERT + Kiwi 후보 + MMR ✅
utils/stock_news_progress.py             # 배치 진행률 JSON + 로그 파싱
utils/batch_scheduler_status.py          # 대시보드 배치 상태 (schtasks 연동)
config/keyword_rules/{stopwords,synonyms,whitelist}
core/main.py                             # /tags/*, /keywords/*, /batch-status/*
static/theme_map.html|js|css             # 탐색 UI
static/dashboard.js                      # 스크리너 표 + 오늘의 키워드 + 배치 진행률
```

---

## 10. 개발 로드맵 (수정)

### Phase 0 — 스파이크 ✅ (구현됨)

- [x] `theme.naver`(또는 fallback) → `theme_tags` + `theme_tag_edges`
- [x] 양방향 조회 API + `/theme-map` UI
- [x] 오늘의 키워드 집계 스케치

### Phase 1 — MVP ✅ / 마무리 중

- [x] DB + 배치 스크립트 + (장중) 1시간 스케줄러
- [x] REST API (`/tags`, `/keywords/today`, …)
- [x] theme-map 시각화 v1
- [x] 뉴스 제목 키워드 룰 + **규칙 파일 외부화**
- [x] **KeyBERT 임베딩 키워드 추출** (`theme_keyword_keybert.py`)
- [x] 대시보드 **오늘의 키워드** 위젯
- [ ] 네이버 실테마 셀렉터 안정화 (fallback 의존 축소)

### Phase 1.5 — 스크리너 enrichment (다음 구현 우선)

목표: **스크리너 후보에 종목별 테마·키워드 표시** (§6.2)

| 순서 | 작업 | 예상 | 상태 |
|------|------|------|------|
| 1 | `theme_map_store.get_latest_map_by_codes(codes)` | 0.5일 | ✅ |
| 2 | `/screener/candidates` 응답 enrichment | 0.5일 | ✅ |
| 3 | 대시보드 스크리너 테이블 컬럼·칩 UI | 0.5일 | ✅ |
| 4 | 배치 커버리지·신선도 점검 (후보 50종목 중 태그 적중률) | 0.5일 | 운영 확인 |
| 5 | (선택) 스캐너 활동 로그에 `theme_text` 한 줄 | 0.5일 | 보류 |

**완료 조건**

- 스크리너 표에서 “편입 후보” 행에 테마/키워드가 보인다.
- 배치 미실행·stale이어도 스크리너 자체는 죽지 않는다.
- 키움 호출 수는 enrichment 추가로 **증가하지 않는다** (DB only).

### Phase 1.6 — 종목별 뉴스 API Stage C ✅ (구현됨)

목표: 네이버 뉴스 검색 API로 종목별 **근거 기사(`tag_articles`) + 기사 제목 키워드**를 매일 보강한다.

| 순서 | 작업 | 예상 | 상태 |
|------|------|------|------|
| 1 | 대상 유니버스: **전체 종목(네이버 시총 목록)** + `biz_date` 스킵(`tag_articles` 존재) | 0.5~1일 | ✅ |
| 2 | `query`/`display`/`sort=date` + 후처리(당일 범위 컷) | 0.5일 | ✅ |
| 3 | 중복제거/429 백오프/실패 종목 재시도 | 0.5~1일 | ✅ |
| 4 | 키워드 추출(KeyBERT)→news_keyword edge→`keyword_daily_stats` 반영 | 0.5일 | ✅ |
| 5 | Windows 스케줄러 등록 + 대시보드 진행률 표시 | 0.5일 | ✅ |

**운영 메모**
- 스케줄: `powershell -File scripts/install_stock_news_batch_task.ps1` (기본 16:30, `--max-stocks-per-run 100` 분할)
- 수동: `python scripts/stock_news_daily_batch.py [--max-stocks-per-run N] [--offset N]`
- 진행률: 대시보드 배치 카드 · `GET /batch-status/stock-news-progress`

### Phase 2 — 탐색 고도화

- [ ] 히트맵·타임라인·공편 종목
- [ ] 수동 태그 (`manual`) UI
- [ ] 키워드 클릭 → theme-map 필터 딥링크
- [ ] (선택) 테마 모멘텀 스코어 — THRESHOLD 실험

### Phase 3 — 선택 (매매·LLM)

- [ ] LLM 태그 보강 (§8)
- [ ] DART 공시 키워드
- [ ] 자동매매 **판정** 연동 (§12) — 표시와 분리

---

## 11. 성공 지표 (v1 — 매매 무관)

| 지표 | 목표 |
|------|------|
| 태그 커버리지 | 네이버 테마 상위 N개 중 편입 종목 적재율 |
| 종목 조회 | 임의 종목 `GET /stocks/{code}/tags` 1초 이내 |
| 태그 조회 | `GET /tags/{id}/stocks` + (선택) 시세 JOIN |
| 데이터 신선도 | `observed_at` 장중 1시간 내 갱신 비율 |
| UI | 태그↔종목 클릭 탐색 without 엑셀 |
| 키워드 신뢰도 | TOP 키워드가 당일 시장 이슈와 정성적으로 일치 |
| **스크리너 enrichment** | 후보 top 50 중 **태그 1개 이상 표시 비율 ≥ 60%** (실데이터 기준, demo 제외) |
| **스크리너 부하** | enrichment 구간 추가 키움 TR = **0** |

---

## 12. 자동매매 연동 (보류 — 아이디어만)

**단계 분리**: (A) 스크리너 **표시** = Phase 1.5 · (B) **매수 판정** = Phase 3

| 단계 | 내용 |
|------|------|
| 관찰 (현재 다음) | 스크리너 표·보유 종목에 테마/키워드 |
| 알림 | “보유 종목이 오늘 상위 키워드에 동시 편입” |
| 후보 합류 | `source=theme` — WEIGHT/THRESHOLD **실험 후** |
| 진입 완화 | 대장주 role + 스코어 — **고위험, 보류** |

`auto_trade_scanner` merge는 **표시가 안정된 뒤** 논의.

---

## 13. 스코어·THRESHOLD (미정 — 결정 전 질문)

1. **스코어가 필요한가?** — 스크리너 enrichment만이면 **표시 top-N**으로 충분.
2. **THRESHOLD는 무엇에?** — (a) UI 상위 K개, (b) 알림, (c) 자동매매 후보. **지금은 (a)+스크리너 표시**.
3. **당일 vs 누적** — 키워드는 **장중 1시간 스냅샷**, 네이버 테마 편입은 당일(또는 최근 N시간) edge.

**권장 순서**: enrichment 표시 → 커버리지 수치 확인 → (선택) 모멘텀 스코어 → (훨씬 뒤) 매매 게이트.

---

## 14. 결론

- 1차 목표는 **종목↔테마/키워드 매핑 DB + 시각화**이고, 곧이은 실사용은 **스크리너 후보에 테마·키워드를 붙이는 enrichment**다.
- 배치는 **장중 1시간(`theme_mart`)** + **장후 일일(`stock_news_daily_batch`)** 로 키워드를 갱신하고, 스크리너는 그 결과를 **DB 조인**만 한다 (키움 부하 방지).
- 키워드 추출은 **KeyBERT(한국어 SBERT) + Kiwi 후보 + 룰 fallback** 3단계로 운영한다.
- 자동매매 **판정**과는 분리한다 — 먼저 “보고 이해”가 되는 UI를 완성한다.
- **완료**: Phase 1.5 (스크리너 enrichment) · Phase 1.6 (종목별 뉴스/키워드 배치) · KeyBERT 도입.
- **다음 액션**: Phase 2 — 히트맵·타임라인·키워드→theme-map 딥링크 · 네이버 실테마 셀렉터 안정화.
