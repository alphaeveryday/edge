# 오픈소스 백필 — 수집 방법 (2026-08-02)

인과귀속의 **표현력 측정**이 가리킨 구멍을 오픈소스로 메운 기록이다. 백필은 이미
돌렸고 데이터는 S3 canonical 에 있다 — 이 문서는 **어떻게 받았는가**만 남긴다.
프로덕션 수집기는 만들지 않았다(그건 data-pipeline 레인 소관). 스크립트는
`edge-dyntool/.tmp/collect/` 에 있는 일회성 백필기다.

## 왜 이걸 모았나

표현력 측정(`statics/expressive.py`)이 005930·2026-07-30 셀에서 자유 산문 가설 5개를
받아 우리 튜플로 사상했더니 **환원율 0%, 방아쇠 5/5 막힘**이 나왔다. 막힌 이유가
전부 같은 부류였다:

```
전일 미국 반도체 지수 하락        (사건타입 목록에 없음)
특정 대형 투자주체의 프로그램 매매  (사건타입 목록에 없음)
국내 기관투자자의 리밸런싱 수요    (특정 사건도 계열 충격도 아님)
환율 급등 사건                   (사건타입 목록에 없음)
증권사 목표주가 하향 리포트 출회   (사건타입 목록에 없음)
```

**비-뉴스 방아쇠**다. 우리 방아쇠는 점(뉴스 사건타입 53)과 계열(계열족 z 발화)
둘뿐이고 그 사이 부류가 통째로 없었다. 그런데 계열족 어휘엔 `거시`·`수급` 이 이미
있었다 — **어휘가 아니라 계산기가 없었던 것**이고, 계산기는 데이터가 있어야 돈다.

## 적재 규약 (전 표 공통)

```
경로   s3://edge-dev-pipeline-lake/canonical/<domain>/<table>/<파티션>/part-{i}.parquet
파티션 market={KR|US|GLOBAL}/trade_date=YYYY-MM-DD   (스냅샷은 as_of_date)
필수열 source_vendor VARCHAR · available_at TIMESTAMP
```

- `available_at` 은 **정보가 관측자에게 도달한 시각**이다. 근사한 표는 각 절에 명시.
- 타입은 `TIMESTAMP` 로 통일한다. pandas `datetime64[ns]` 를 DuckDB `COPY` 로 그냥
  흘리면 parquet 이 `TIMESTAMP_NS` 가 되고, 표마다 타입이 갈리면 UNION·조인이 깨진다.
  `COPY` 의 SELECT 에서 `CAST(available_at AS TIMESTAMP)` 로 명시할 것.
- **ticker 는 접미사 없는 6자리**(`005930`). 원본 심볼은 `source_symbol` 에 보존.
  레이크의 `v_instrument.ticker`·`canonical/price_daily` 가 그 규약이라, 안 맞추면
  조인이 통째로 안 된다.
  ⚠️ **예외 하나** — `intraday_5m` 의 `part-sector-index.parquet` 은 `ticker` 가 4자리
  KRX 업종코드다(ALPHA-941). 지수는 종목이 아니라 `v_instrument` 에 없고 조인 대상도
  아니다(섹터 층 계열로 따로 소비된다). 종목코드와 서로소라 같은 파티션에 공존해도
  겹치지 않고, 가르는 축은 `source_vendor='1m_rollup_sector'` 다.
- 금액 단위는 **원(KRW)**. KIS 가 백만원 단위로 주면 1e6 곱해서 넣는다.
- 쓰기는 DuckDB 로:
  ```python
  c.execute("INSTALL httpfs; LOAD httpfs;")
  c.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, "
            "CHAIN 'sso;config;env', PROFILE 'work', REGION 'ap-northeast-2')")
  c.execute("COPY (SELECT …) TO 's3://…' (FORMAT parquet, "
            "PARTITION_BY (market, trade_date), OVERWRITE_OR_IGNORE true)")
  ```

---

## 1. 환율·해외지수·금리 — FMP stable

| 표 | 행 | 기간 | 내용 |
|---|---|---|---|
| `canonical/market_data/fx_daily` | 1,464 | 2025-06-01~2026-07-31 | USDKRW·USDJPY·EURUSD·DXY |
| `canonical/market_data/index_daily` | 1,758 | 2025-06-02~2026-07-31 | SOXX·SMH·QQQ·SPY·^NDX·^GSPC |
| `canonical/market_data/rates_daily` | 292 | 2025-06-02~2026-07-31 | 미 국채 12개 만기 |

```
GET https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=<SYM>&from=&to=&apikey=
GET https://financialmodelingprep.com/stable/treasury-rates?from=&to=&apikey=
키: aws --profile work secretsmanager get-secret-value \
      --secret-id edge-dev-data-pipeline/fmp/api-key --query SecretString --output text | jq -r .apikey
재현: python .tmp/collect/fx_index_rates.py
```

주의 셋:
- **`change_pct` 를 그대로 쓰면 안 된다.** FMP 의 `changePercent` 는 (종가−당일시가)이지
  전일 대비가 아니다. 방아쇠가 요구하는 건 전일 대비라 `LAG` 로 재계산했다.
  (LAG 프라이밍용으로 시작일보다 한 달 앞부터 받아 잘라낸다.)
- `^SOX`(필라델피아 반도체 지수 본체)는 stable 이 **빈 배열**을 준다. 대리 ETF
  `SOXX`·`SMH` 로 대체했고 둘 다 풀커버라 실질 결손은 없다.
- `DXY` 티커는 없다. ICE 원본 `DX-Y.NYB` 로 받아 `symbol='DXY'` 로 정규화했다.
  (`USDX` 는 응답이 오지만 종가 25.54 로 달러지수가 아닌 무관한 ETF다 — 오염 주의.)

`available_at` = 미국장 마감을 KST 로 환산(zoneinfo, DST 반영). 미국 종가는 **항상
한국 익일 09:00 개장 전**에 관측 가능하므로 갭 공변량·방아쇠로 누수 없이 쓸 수 있다.
fx 는 17:00 ET, rates 는 16:00 ET 고시로 근사.

## 2. 투자자별 수급·프로그램매매 — KIS

| 표 | 행 | 기간 | 내용 |
|---|---|---|---|
| `canonical/market_data/investor_value_daily` | 1,356,004 | 2025-06-02~2026-07-31 | 366종목 × 285일 × 13 투자자유형 (long) |
| `canonical/market_data/program_trading_daily` | 102,652 | 2025-06-02~2026-07-31 | 종목별 프로그램 순매수 |

```
KIS OAuth: aws --profile work secretsmanager get-secret-value --secret-id edge-dev-data-pipeline/kis/oauth
재현: python .tmp/collect/krx_flow.py --table both --source kis
```

- **KIS 투자자 금액은 백만원 단위다.** KRX 실측 교차검증: 005930 2026-07-30
  기관합계 매도 KIS `3,088,944` × 1e6 = KRX `3,088,943,833,250`원. 원 단위로 환산해 저장.
- `investor_type` 13종은 기존 `canonical/market_data/investor_flow_daily`(KIS·wide)의
  영문명에 맞췄다 — 같은 개념에 두 번째 표기를 만들면 매핑표가 또 필요해진다.
- **차익/비차익 구분은 못 구했다.** KIS 종목별 프로그램매매는 전체합계만 주고
  차익·비차익 분해는 시장 단위로만 공표된다. `arbitrage_net`·`non_arbitrage_net` 은
  전 행 NULL 이다 — 0 으로 채우면 "차익거래 0원"이라는 거짓이 된다.
- `available_at` = 그날 18:00 KST 근사(장 마감 후 집계 공표).

### pykrx·KRX 직접 수집은 실패했다 (기록)
`pykrx` 는 프로그램매매 함수가 없다(`dir(pykrx.stock)` 전수 확인). KRX 정보데이터
시스템은 2026-08 현재 MDCSTAT 조회에 **회원 로그인**을 요구하고(비로그인 HTTP 400
`LOGOUT`), 메뉴가 SPA 라 `bld` 파라미터를 HTML 에서 못 딴다. 헤드리스 브라우저는
`blockError_01` 로 차단된다. `bld` 브루트포스(약 250 POST)는 **IP 차단을 유발했다** —
하지 말 것. 자격증명이 이미 있는 KIS 로 동등한 데이터를 받는 편이 빠르다.

## 3. 애널리스트 목표주가·등급 — FMP stable

| 표 | 행 | 기간 | 내용 |
|---|---|---|---|
| `canonical/reports/analyst_target` | 1,371 | 2025-06-02~2026-07-31 | **US 반도체 30종목** 개별 목표주가 변경 |
| `canonical/reports/rating_distribution` | 3,334 | 2025-06-01~2026-07-01 | KR 251종목 월별 등급분포 |

```
GET /stable/price-target-news?symbol=<SYM>&page=&limit=100      개별 이벤트
GET /stable/grades-historical?symbol=<SYM>&limit=                월별 등급분포
재현: python .tmp/collect/consensus.py all
```

- `analyst_target.available_at` 은 **근사가 아니다**(FMP `publishedDate` 원본).
- `action` 분포: raise 973 / lower 161 / maintain 1 / NULL 236. NULL 은 제목에 이전
  목표가도 방향 동사도 없는 경우 — 억지 추정하지 않고 NULL 로 뒀다.
- KR 등급분포는 **월 단위**라 "특정일 리포트 출회" 방아쇠로는 못 쓴다. 전월 대비
  `consensus_score` 악화 감지용이다.

### 한경 컨센서스는 수집하지 않았다 (robots 차단)
`http://consensus.hankyung.com/robots.txt` 가 `User-Agent: * / Disallow: /` 전면
차단이다(27바이트, http·https 동일). 우회하지 않았다. `finance.naver.com` 도
`User-agent: *` 블록이 `Disallow: /` 이고 `Allow: /research/` 는 네이버 자체봇
`yeti` 전용이라 대체 불가.

### KR 개별 애널리스트 리포트는 벤더에 없다 (정직한 부재)
FMP 5개 엔드포인트(`price-target-news`·`grades-news`·`price-target-summary`·
`price-target-consensus`·`grades-consensus`) × KR 4종목(005930·000660·373220·035420)
전부 빈 배열. 같은 엔드포인트가 NVDA·MU·AVGO 에는 100행씩 준다 — 엔드포인트 오용이
아니라 **벤더의 KR 커버리지 공백**이다. 빈 표를 만들어 있는 척하지 않았다.

### UPSTAGE_API_KEY 부재
PDF 본문 파싱 경로는 코드로 만들어 뒀으나 키가 환경변수·secretsmanager 양쪽에 없어
실행하지 않았다. 어차피 원문 출처(한경)가 robots 차단이라 이중으로 불가하다.
**키를 받으면** `.tmp/collect/consensus.py` 의 `upstage_stage2` 경로가 살아난다.

## 4. 5분봉 정규화 — raw → canonical

| 표 | 행 | 기간 | 내용 |
|---|---|---|---|
| `canonical/market_data/intraday_5m` (KR) | 63,866,933 | 2022-11-01~2026-07-31 | 1,271종목 · 916거래일 |
| `canonical/market_data/intraday_5m` (US) | 87,615,393 | 2024-01-02~2026-07-31 | 2,015종목 · 647거래일 |

```
재현 KR:  python .tmp/collect/normalize_intraday.py --market kr --threads 3 --memory-gb 2
재현 US:  python .tmp/collect/normalize_intraday.py --market us --from 2024-01-01 --threads 3 --memory-gb 2
최신분 :  python .tmp/collect/fetch_5min_gap.py --market {kr|us} --from --to --tag <조각>  (1주씩)
          → normalize_intraday.py --market {kr|us} --from <시작일>
```

- 입력은 `raw/kr_intraday/fmp_5min/*.parquet`(종목당 1파일)와 `raw/fmp_5min_us/`.
- `ts` 는 KR=KST naive(09:00~15:30), US=ET naive(정규장 09:30~15:55, 프리·애프터 없음).
- `available_at = ts + 5분` — 봉이 닫히는 순간이라 **근사가 아니다**.
- 검증: 일봉 종가 == 그날 마지막 5분봉 종가가 **287/287 = 100%** 일치(2026-07-30 KR).
  `(ticker, ts)` 중복 0행.
- 비용: 단문 `COPY` 는 하루치(52초)는 되지만 전체는 OOM 이다(가용 RAM 2.1GB 에서
  63M행 정렬 + 900파티션 동시 쓰기). **로컬 stage → 월 단위 publish** 2단계로 쪼갰다.
  실측 KR 12분 · US(2024+) 15분. US 전체 이력은 [INFERENCE] 약 2시간·645M행.
- `raw/kr_intraday/fmp_1min` (242,326,958행)은 **정규화하지 않았다**. 구조는 5분봉과
  동일하고 마지막 날(2026-07-20)이 미완이다.

### data-pipeline 레인에 보고할 버그
raw 5분봉이 최신이 아니었다 — KR 2026-07-16, US 2026-06-26 에서 끊김. **둘 다
2026-07-25 수집분인데도 그렇다.** 원인: FMP stable `historical-chart` 는 응답 행
상한이 있어 요청 구간이 길면 오래된 쪽이 아니라 **최신분만 주고 나머지를 자른다.**
`005930.KS` 를 07-17~07-31 로 부르면 07-22 부터만 오고, 07-17~07-21 로 좁히면
07-20·07-21 이 나온다. 원 수집기가 같은 상한에 잘린 것으로 보인다.
**대응**: 1주 단위로 쪼개 재수집. 정식 수집기도 같은 방식이 필요하다.

---

## 결과 — 레이크에 붙은 것

`statics/duck.py` 의 `S3_SETS` 에 명시 등록한다(자동 전량 부착이 아니다):

```
s3_fx_daily · s3_index_daily · s3_rates_daily
s3_analyst_target · s3_rating_dist
s3_investor_value · s3_program_trading
s3_intraday_5m
```

그리고 두 가지가 열렸다:

1. **계열 방아쇠 2종 추가** — `paneltest.macro_z()`·`flow_z()`. 계열족 9 중 계산되는
   것이 2(가격잔차·거래량) → **4**(거시·수급 추가). 어휘 확장이 아니라 계산기 확장이라
   상류 온톨로지를 안 건드린다. 둘 다 **직전 거래일**을 쓴다 — 미국 종가는 KST 익일
   05:00, 투자자 집계는 당일 18:00 에 공표되므로 오늘 장중의 방아쇠로 쓰려면 전일이어야
   PIT 가 선다.
2. **`bars_5m` 이 S3 canonical 로 승격** — 로컬 2종목 → **1,271종목**. 셀 배치 평가의
   전제였다(그전까지 라이브 검증이 종목 하나짜리 일화에 머물렀다).

측정 셀 직격 확인(005930 2026-07-30):
```
직전 거래일 SOXX -5.38% · SMH -4.79%   available_at 07-30 05:00 KST < 09:00 개장
같은 날 미국 반도체 목표주가 하향 12건  (QCOM 7개 증권사 동시)
거시 z = 2.91 ≥ 2  →  이 셀에서 처음으로 계열 방아쇠 자격이 섰다
   (이전엔 가격잔차 -0.04 · 거래량 1.26 으로 둘 다 미달, "계열 이상 없음")
```
