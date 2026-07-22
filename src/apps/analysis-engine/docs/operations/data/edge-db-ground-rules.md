---
doc_type: design
status: Accepted
owner: data-platform
created: 2026-07-11
updated: 2026-07-11
related:
  - ../lake/market-data-lake.md
---

> **모듈 스펙:** DB/스토리지 공통 규칙 — `src/alphamale/analytics/common/db/client.py` 와 패키지형 분석 모듈이 공유하는 DB/env 규칙.
> **상태:** CURRENT — packaged DB client와 현재 문서 레이아웃 기준.
> **드리프트:** 일부 스크립트에 PGPASSWORD 하드코딩 기본값 잔존(`research/analytics/event_study/*`, `src/alphamale/analytics/context/price_export.py`, 기타 legacy research drivers`) — 문서의 'no secrets in code' 원칙 위반, 코드 정리 필요.
> **통합:** 구 'FMP Korean Stocks Coverage Review.md'는 아래 [부록: FMP 한국주 커버리지]로 흡수됨.

# Edge DB Ground Rules

기준 DB: PostgreSQL `edge`  
기준 schema: `etf`

## 1. Source of truth

- 운영 분석 데이터의 source of truth는 PostgreSQL `edge.etf`다.
- 로컬 CSV/Parquet/DuckDB 산출물은 재현·검증·시각화용 artifact이며, 운영 조회 기준은 `edge.etf` 테이블이다.
- SQL과 문서에서는 테이블을 항상 schema-qualified 형태로 표기한다. 예: `etf.price_intraday`, `etf.fmp_kr_stock_industry_map`.

## 2. 한국 종목코드 표준

한국 주식의 내부 표준 종목코드 표기는 전부 **6자리 zero-padded 문자열**이다.

| 항목 | 규칙 |
|---|---|
| 내부 표준 컬럼명 | `ticker` |
| 타입 | `TEXT` / `VARCHAR`, 절대 integer 금지 |
| 표기 | 6자리 zero-padded KRX short code |
| 예시 | `005930`, `000660`, `035420` |
| 금지 | `5930`, `005930.KS`, `KRX:005930`, 숫자형 `5930` |
| cross-market key | `(market, ticker)` |

원칙:

- canonical 가격/팩터/수익률 테이블에서 `market = 'KR'`인 `ticker`는 항상 suffix 없는 6자리 문자열이다.
- leading zero는 식별자의 일부다. 숫자 변환으로 제거하면 안 된다.
- 외부 벤더 심볼은 canonical `ticker`에 섞지 않는다.
- source-specific mapping/staging 테이블이 컬럼명 통일을 위해 `ticker`를 쓰더라도, 6자리 보장이 깨지는 값은 품질 플래그로 표시하고 canonical 조인 전에 필터링한다.

## 3. 외부 벤더 심볼 처리

| 소스 | 예시 | 저장 원칙 |
|---|---|---|
| KRX / pykrx | `005930` | canonical `ticker`로 사용 가능. `str.zfill(6)` 적용 |
| FMP 로컬 심볼 | `005930.KS`, `091990.KQ` | `.KS`/`.KQ` suffix 제거 후 local code만 보존 |
| FMP local code | `005930`, `28513K`, `NA` | mapping table에서는 컬럼명 통일을 위해 `ticker`로 저장하되, 비숫자 값은 `mapping_quality_flags`로 표시 |
| yfinance | `^KS11`, `005930.KS` | `source_symbol`에 보존하고 내부 조인에는 별도 `index_code` 또는 canonical `ticker` 사용 |

## 4. FMP 한국 산업분류 테이블

현재 edge DB 적재 테이블:

```text
edge.etf.fmp_kr_stock_industry_map
```

row grain:

```text
1 row = 1 FMP Korean local listed security/share class
primary key = (market, ticker)
```

주요 컬럼:

| 컬럼 | 의미 |
|---|---|
| `market` | 내부 시장 코드. 현재 값은 `KR` |
| `ticker` | suffix 없는 FMP local listing/share-class code. `.KS`/`.KQ` 없음 |
| `company_name` | FMP 회사명 |
| `listing_market` | 정규화 상장시장. FMP `KSC → KOSPI`, `KOE → KOSDAQ` |
| `fmp_sector_key`, `fmp_sector` | FMP sector key/value |
| `fmp_industry_key`, `fmp_industry` | FMP industry key/value |
| `fmp_classification_path_key` | `sector::industry` path key |
| `market_cap` | 산업 내 정렬/필터용 시가총액 |
| `is_primary_share_class` | 같은 회사명 내 시총 최대 share class 여부 |
| `mapping_quality_flags` | `ok`, `non_numeric_ticker`, `missing_market_cap` 등 원천 예외 플래그 |

조회 예시:

```sql
-- 특정 종목의 산업
SELECT market, ticker, company_name, fmp_sector, fmp_industry
FROM etf.fmp_kr_stock_industry_map
WHERE market = 'KR'
  AND ticker = '005930';

-- 특정 FMP industry에 속한 종목
SELECT market, ticker, company_name, listing_market, market_cap
FROM etf.fmp_kr_stock_industry_map
WHERE fmp_industry_key = 'semiconductors'
ORDER BY market_cap DESC;

-- sector까지 고정한 안전 조회
SELECT market, ticker, company_name, market_cap
FROM etf.fmp_kr_stock_industry_map
WHERE fmp_classification_path_key = 'technology::semiconductors'
  AND mapping_quality_flags = 'ok'
ORDER BY market_cap DESC;
```

주의:

- 이 테이블에는 FMP 원본 심볼 `005930.KS`를 저장하지 않는다.
- `.KS`/`.KQ` suffix는 제거되어 `ticker = '005930'` 형태로 저장된다.
- `ticker`는 FMP local listing/share-class code에서 온 값이라 `28513K`, `0010F0`, `NA` 같은 비숫자 예외가 있을 수 있다.
- canonical 6자리 KR 종목코드와 조인할 때는 `mapping_quality_flags = 'ok'` 또는 `ticker ~ '^[0-9]{6}$'` 조건을 사용한다.

## 5. 산업분류/GICS ground rule

- FMP에서 확인한 분류 필드는 `sector`, `industry` 두 단계다.
- FMP `industry`는 공식 GICS `Sub-Industry`가 아니다.
- FMP 문서에서 공식 GICS code, Industry Group, Sub-Industry, 4계층 GICS 전체 매핑은 확인되지 않았다.
- 따라서 FMP 분류는 broad grouping / exploratory aggregation / donor filter 보조 용도로만 사용한다.
- 공식 GICS exposure나 세분류 분석이 필요하면 별도 GICS 라이선스/벤더 매핑 테이블을 사용한다.

## 6. 날짜와 시간

- 일별 데이터의 기본 날짜 컬럼은 `trade_date`다.
- `trade_date`는 거래소 현지 거래일 기준이다.
  - KR: `Asia/Seoul`
  - US: `America/New_York`
- intraday timestamp는 UTC timestamp 컬럼을 보존하고, 일봉화할 때만 현지 거래일로 변환한다.
- timestamp 컬럼은 가능하면 `TIMESTAMPTZ`를 사용한다.

## 7. 숫자와 결측

- 식별자는 숫자처럼 보여도 `TEXT`로 저장한다.
- 금액/시가총액/거래대금처럼 큰 정밀도가 필요한 값은 `NUMERIC`을 우선한다.
- 수익률, 회귀계수, 상관계수처럼 계산 결과는 `DOUBLE PRECISION`을 사용할 수 있다.
- 결측은 `NULL`로 둔다. 의미 없는 `0`, 빈 문자열, `n/a`로 대체하지 않는다.
- source anomaly는 삭제보다 플래그 보존을 우선한다.

## 8. 테이블 설계 원칙

- PK는 자연키를 우선한다. 예: `(market, ticker, trade_date)`, `symbol`.
- derived snapshot table은 `loaded_at` 또는 `created_at`을 둔다.
- 원천 벤더 필드는 가능한 한 보존하고, 내부 표준 필드와 분리한다.
- 테이블명은 lower snake_case를 사용한다.
- source-specific 테이블은 소스명을 prefix에 포함한다. 예: `fmp_kr_stock_industry_map`.
- 분석용 long-form table은 query 방향을 기준으로 grain을 정한다. stock→industry와 industry→stocks가 모두 중요하면 종목 1행 구조가 기본이다.

## 9. 보안

- 비밀번호, API key, token은 코드, 문서, DB row 값에 저장하지 않는다.
- 문서에는 secret 값이 아니라 필요한 env var 이름만 적는다.
- Postgres 연결은 운영 환경변수 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`를 우선한다.

---

## 부록: FMP 한국주 커버리지 결론 (구 *FMP Korean Stocks Coverage Review*)

- 기준 산출물: `data/processed/analytics/analysis_outputs/fmp_kr_coverage_summary_20260619_172627.json`.
- **분류 메타데이터(sector/industry):** 한국 로컬 상장주 커버리지 사실상 100% → 넓은 범주화 용도로는 사용 가능.
- **뉴스 커버리지:** `.KS`/`.KQ` 심볼 기준 최근 12개월 0건 / 2019년 이후 0건, 6자리 코드 fallback도 극소수 → **한국 상장주 이벤트 스터디 뉴스 원천으로 부적합**.
- **결론:** KR 뉴스는 FMP가 아니라 BigKinds/stockinfo7/Google News RSS(`uv run alphamale news acquisition google-rss`) + `etf.news_articles`로 수집. FMP는 US 가격 적재(`uv run alphamale analytics context fmp-prices`)에만 사용. live consumer 없음(스펙 아님, 결정 근거).
