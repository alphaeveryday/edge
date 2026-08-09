# 데이터 소스 통일 스펙 (ALPHA-879)

> **스펙만 있다 — 구현 없음.** 분석엔진이 소비하는 데이터 표면이 어디서 오는지 전수 조사하고,
> 어느 존이 정본인지, 수작업 백필로만 존재하는 것을 어떤 순서로 상시 수집으로 전환할지 정한다.
> 조사 근거는 전부 레포 코드·문서다(레이크 실측 없음) — 실측이 필요한 주장은 **[실측 필요]** 로 표기한다.
>
> 표면 목록의 코드 정본: `analysis-engine/src/edge_analysis/statics/duck.py`
> (`RDB_TABLES`·`S3_SETS`·`BACKFILL_SETS`). 경로 규약의 코드 정본:
> `data-pipeline/src/data_pipeline/lake/storage.py`.

## 1. 산재 현황 전수표

분석(CausalLake)이 읽는 원천은 네 무리다: **RDB**(Postgres 클램프 뷰) · **S3 canonical/feature**
(파이프라인 산출) · **S3 draft/curated·raw**(수작업 적재) · **백필 10세트**
(`s3://…/analysis/backfill/<name>.parquet`, 로컬 `.tmp/causal-backfill` 폴백).

PIT 안전성 표기: ✅ = 시점 클램프/파티션이 선견을 구조적으로 막음 · ⚠️ = 근사/부분 보장 ·
❌ = 보장 없음(사실이 늦게 바뀌면 선견).

### 1.1 파이프라인이 상시 생산하는 표면 (정본 경로 있음)

| 표면 | 존·경로 | 생산자 | 주기 | PIT |
|---|---|---|---|---|
| RDB 19표 (`price_daily`·`source_event`·`document`… `RDB_TABLES`) | Cloud Event Store (Postgres) | data-pipeline SFN 4레인(시장·뉴스·공시·장중수급) + 1분 레인 | 일배치·하루 3~5슬롯·분 단위 | ✅ 자동 클램프 뷰(`bind_day`) — 단 클램프 열 없는 시점 불변 차원은 ❌ 로 `coverage()` 가 명시 |
| `bars_5m` (5분봉) | **정본 = Glue Iceberg** `gl.market_data_kr.edge_intraday_5m` (fmp 백필 + 깊은 재수집 + 1분 롤업 3원천 합본) · 폴백 = `canonical/market_data/intraday_5m` 합집합 | 1분 레인 롤업(2026-08-04~, `ROLLUP_FROM`) + 백필 writer 들(§1.3) | 장중 5분 버킷 + EOD 확정(`rollup-minute-session`) | ✅ `available_at = ts+5분` · 신선도 판정(착지 ≥100종 + 시장 프록시 069500) 미달 시 canonical 폴백 |
| `s3_price_daily` | `canonical/market_data/price_daily` | `normalize-price` (KIS KR·FMP US) | 일배치 (FMP 는 한도로 토글 off — ALPHA-558) | ✅ trade_date 파티션 |
| `s3_investor_flow` | `canonical/market_data/investor_flow_daily` | `normalize-investor` (KIS EOD) | 일배치 | ✅ |
| `investor_flow_intraday` | `canonical/market_data/investor_flow_intraday` | 장중 수급 레인(평일 5슬롯) | 하루 5슬롯 | ✅ asof_slot 축 분리로 잠정/확정 구분 |
| `s3_etf_nav` | `canonical/market_data/etf_nav` | `normalize-etf-nav` (KIS) | 일배치 | ✅ |
| `s3_news_articles` / `s3_assertions` | `canonical/news/news_articles` / `feature/news/assertions` | 뉴스 레인(하루 3슬롯) + 1분 뉴스 canonical writer | 슬롯·분 단위 | ⚠️ `available_at` 이 다수 행에서 적재 시각(τ 승격은 사이드카 의존 — §1.4) |
| `s3_etf_holdings` / `s3_etf_profile` | `canonical/holdings/etf_holdings` / `canonical/reference/etf_profile` | `normalize-etf`(KRX·FMP) / etf-profile(KIS) | 일배치 | ✅ as_of_date · 단 duck 은 유니버스 뿌리 필터 없이 프리픽스 전체를 읽음(🔴 storage.py 주석) |
| `s3_supply_fact` / `s3_segment_fact` | `canonical/disclosures/*` | 공시 레인 | 일배치 | ✅ report_date |

### 1.2 수작업 일회성 백필 — canonical 경로에 있으나 **포워드 수집 없음**

전부 `docs/design/open-source-backfill.md`(2026-08-02) 의 일회성 스크립트
(`edge-dyntool/.tmp/collect/`, 레포 밖) 산출이다. 존은 canonical 이지만 **생산자가 상시가 아니다**
— 마지막 적재일 이후는 공백이고, 공백은 조용하다(뷰는 성공하고 0행).

| 표면 | 경로 | 기간(적재 시 실측) | 갱신 | PIT |
|---|---|---|---|---|
| `s3_fx_daily` | `canonical/market_data/fx_daily` | 2025-06-01~2026-07-31 | **없음** | ⚠️ available_at = 미국장 마감 KST 환산(근사) |
| `s3_index_daily` | `canonical/market_data/index_daily` | 2025-06-02~2026-07-31 | **없음** | ⚠️ 동일 근사 |
| `s3_rates_daily` | `canonical/market_data/rates_daily` | 2025-06-02~2026-07-31 | **없음** | ⚠️ 16:00 ET 고시 근사 |
| `s3_investor_value` | `canonical/market_data/investor_value_daily` | 2025-06-02~2026-07-31 (1.36M행) | **없음** | ⚠️ 18:00 KST 근사 |
| `s3_program_trading` | `canonical/market_data/program_trading_daily` | 2025-06-02~2026-07-31 | **없음** | ⚠️ 동일 · 차익/비차익 NULL |
| `s3_analyst_target` | `canonical/reports/analyst_target` | 2025-06~2026-07 (US 30종) | **없음** | ✅ publishedDate 원본 |
| `s3_rating_dist` | `canonical/reports/rating_distribution` | 2025-06~2026-07 (KR 월별) | **없음** | ⚠️ 월 grain |
| `s3_intraday_5m` 의 fmp 백필분 | `canonical/market_data/intraday_5m` (KR ~2026-07-31·US) | 2022-11~2026-07-31 | 이후는 1분 롤업이 승계 | ✅ |

### 1.3 draft/curated·raw — 수작업 적재, 승격 전 존

| 표면 | 경로 | 생산자 | 갱신 | PIT |
|---|---|---|---|---|
| `s3_dg_*` 6셋 (market·financials·flow·price·consensus·items) | `draft/curated/source=dataguide/…` (gzip CSV) | DataGuide 수작업 적재 (레포 밖) | 수작업 — market_daily 는 248거래일분, as_of 주간 파티션(consensus) [실측 필요: 마지막 as_of] | ⚠️ trade_date/as_of 스냅샷이라 구조적 선견은 없으나 갱신이 사람 손 |
| `s3_kr_5min` / `s3_us_5min` | `raw/kr_intraday/fmp_5min/*.KS.parquet` / `raw/fmp_5min_us/` | 수작업 FMP 수집 | 중단 (KR 2026-07-16·US 2026-06-26 절단 — FMP 응답 상한 버그, open-source-backfill §4) | ⚠️ |
| `s3_statement_line` 등 draft/canonical Iceberg 7셋 | `draft/canonical/…` | `backfill/run.py` (HuggingFace dartlab) | 수작업 백필 | ❌ dartlab 은 최종 확정치만(정정 이력 없음 — README "이 입력은 PIT 가 아니다") |
| `s3_estimate_line` | `draft/canonical/estimates/estimate_line` | — | **메타데이터만, 데이터 0파일** (실적재는 `s3_dg_consensus` 로 감) | — |

### 1.4 백필 10세트 (`BACKFILL_SETS`) — 분석 전용 parquet, 전부 수작업

`s3://<lake>/analysis/backfill/<name>.parquet` 하나씩. 하류 SQL 이 이름으로 직접 참조하므로
목록이 곧 계약이고, 0행이면 **질의가 성공한 채 0행**이 돌아온다(가장 위험한 실패 양식 —
duck.py `backfill_sources` docstring).

| 세트 | 생산자 | 원천 | 갱신 | PIT |
|---|---|---|---|---|
| `sector_index` · `sector_member` | `statics/krxsector.py` (수동 CLI) | pykrx (KRX 업종지수 일봉 + 분기 분류 스냅샷) | **수작업 — 2022-11-01~2026-08-03 적재 후 공백, 포워드 없음** [실측 필요: 정확한 말일·업종 수(코드상 KOSPI+KOSDAQ 업종, 티켓 기록 45업종)] | ✅ 분류는 날짜 인자 PIT · 일봉은 종가 시계열 |
| `layers_daily` | **레포 밖 산출물** (786k행 — 티켓 기록) | kind=market 1 · sector(ETF) 80 · stock 856 · us 6 의 일봉 (2026-08-07 실측 주석) | **수작업 · 생산 경로가 레포에 없음** | ⚠️ 일봉 종가라 구조 선견은 없으나 **이름 오염 이력**(41/80 — `interval.py`·`layers.py`: 091160 이름이 'SK hynix Inc.') 으로 이름은 못 믿어 `s3_etf_profile` 로 대체 중 |
| `pit_daily` | `statics/pit.py` (수동) | curated DataGuide market_daily 를 넓은 형식으로 접음 (248일×4,054종목) | 수작업 — curated 갱신에 종속 | ✅ 파티션 자체가 특정시점 스냅샷 |
| `fin_annual` | `statics/fin.py` (수동) | curated DataGuide 재무 (1981~) | 수작업 | ✅ `available_from`(결산 후 90일 보수 규칙) 행 내장 |
| `flow_daily` | `statics/flowhist.py` (수동) | curated DataGuide 투자자별 매매 (2022~) | 수작업 | ✅ |
| `tau_sidecar` | `statics/tau_sidecar.py` (수동·누적) | raw 뉴스 ndjson 재파싱 (초 단위 발행시각) | 수작업 — 안 돌리면 τ 가 09:00 뭉침 폴백 | ✅ (KST naive 발행시각) |
| `us_market` · `fx_usdkrw` | `statics/backfill.py` (수동) | FMP 일봉 (며칠치) | 수작업 | ⚠️ available_at 장마감 근사 |
| `etf_holdings_fmp` | 수작업 | FMP holdings | 수작업 | ⚠️ [실측 필요: 마지막 적재일] |

## 2. 소스 통일 원칙

기존 규약을 새로 만들지 않고 **이미 코드에 있는 원칙을 명문화**한다.

1. **canonical 이 정본 존이다.** canonical = 벤더 원본(raw)의 결정론적 정규화, 멱등,
   run_id 없음. raw 에서 언제든 무료로 재생성된다. feature = 비결정적·유료 추론(LLM) 산출
   — 존이 갈리는 근거는 라이프사이클이다(storage.py 서문). draft/ 는 승격 전 격리 접두사다
   (README "백필 — 포워드와 격리된 재구축 경로": 좌표 3종 source=·run_id=·접두사).
2. **경로 규약의 SSOT 는 `lake/storage.py` 빌더다.** 신규 데이터셋은 빌더 추가가 선행이고,
   다른 곳에서 경로 문자열을 조립하지 않는다. §1.2 의 수작업 canonical 경로들은 빌더 없이
   레포 밖 스크립트가 직접 쓴 것이라 **규약 위반 상태다** — 상시화(§3) 시 빌더를 세우고
   그 빌더 경로로 수렴한다(기존 경로와 동일하게 정의해 데이터 이동은 없게).
3. **표면(데이터셋)당 writer 는 하나다.** DB 는 ADR-0005(db-as-contract)의 테이블 소유권
   (implementation.md §4), 레이크는 같은 원칙의 확장이다 — `price_movement_trigger` 단일
   writer, `intraday_5m` 의 `rollup.writer_owns()` 경계(파티션을 통째로 나눠 소유, 겹치면
   foreign 가드가 정지). 수작업 백필이 상시 파이프라인과 같은 데이터셋을 쓰게 되면 반드시
   이 소유권 경계를 먼저 정한다.
4. **잠정과 확정, 축이 다르면 데이터셋을 가른다.** investor_flow_daily↔intraday,
   etf_nav↔etf_inav 의 기존 규율. 업종지수도 일봉과 분봉은 다른 데이터셋이다.
5. **수렴 방향.**
   - §1.2 (수작업 canonical) → **경로는 유지, 생산자만 상시화**(§3). 존 이동 없음.
   - §1.4 (analysis/backfill 10세트) → 원천이 canonical 로 상시화되는 세트부터 **canonical
     직독으로 대체하고 세트를 폐지**한다. `us_market`·`fx_usdkrw` 는 이미 `s3_index_daily`·
     `s3_fx_daily` 와 중복이라 포워드가 서면 즉시 폐지 후보다. `sector_index`·`sector_member`
     는 canonical 데이터셋 신설(§4.1)로 대체. `layers_daily` 는 §3-③. `tau_sidecar` 는
     ALPHA-696 계열 재적재가 완료되면 폐지(정본 τ 가 canonical·RDB 에 서는 시점).
     `pit_daily`·`fin_annual`·`flow_daily` 는 curated DataGuide 종속이라 **DataGuide 갱신
     체계가 정해지기 전까지 유지**(폐지 불가) — 후속 판단 항목으로 남긴다.
   - draft/curated (DataGuide) → 당장 승격하지 않는다. 라이선스·갱신 주체가 정해지지 않은
     수작업 적재를 canonical 로 올리면 "정본인데 안 갱신되는" §1.2 문제를 복제한다.
6. **완전성은 collection_log 계약으로 판정한다.** 모든 상시 스텝은
   `operations_archive/collection_logs/source=…/dataset=…/started_date=…/run_id=…/log.json`
   에 `"ops": {"records_out", "failed_records"}` 봉투를 남기고, ops 카탈로그 등록 시
   `data_status`(UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID)가 원장에 올라간다.
   수작업 스크립트에는 이 계약이 없다 — 상시화가 곧 관측 가능화다.

## 3. 백필→포워드 전환 목록 (우선순위 순)

우선순위 기준: (a) 공백이 이미 자라고 있는가(소급 불가면 가중) (b) 분석 표면이 직접 소비하는가
(c) 기존 파이프라인 재사용으로 싼가.

| # | 항목 | 왜 지금 | 난이도 |
|---|---|---|---|
| ① | **KRX 업종지수 일봉 + 업종 분류 스냅샷** (`sector_index`·`sector_member` 공백 메우기 + 상시화) | 2026-08-03 이후 공백이 매일 자란다. 일봉 경로의 섹터층(`layers._krx_sector_candidate`)과 kbeta·attribute·tool_peer 가 직접 소비 — 공백 구간은 섹터층이 조용히 부재 | 하 — pykrx 소급 가능이라 급하지 않은 대신 싸다 |
| ② | **KRX 업종지수 분봉** (장중 섹터층의 전제) | ✅ **수집 배선 완료**(ALPHA-887) — KIS 업종 TR `FHKUP03500200` 이 실재한다(시장구분 `U`). 45종을 `sector_index_minute` dataset 으로 1분 레인에 편입했다. ⚠️ 레포 곳곳의 "KRX 지수는 5분봉이 없다"(layers.py 등)는 **분봉 부재의 근거가 못 된다** — 다만 **소급은 진짜 불가**하다(소급 TR 이 일봉으로 degrade). 남은 일은 **소비 전환**이다: 구간 모드 섹터층이 아직 섹터 ETF 대체를 쓴다 | 중 — 수집 완료, 소비 표면 전환이 잔여 |
| ③ | **`layers_daily` 정규 생산 경로 또는 폐기** | 레포 밖 산출물 786k행에 시장·섹터·종목·미국 층 전부가 매달려 있고 이름 오염(41/80) 이력. **권고: 폐기·유도 대체** — market/sector/stock 층은 `canonical/market_data/price_daily`(KIS 가 ETF 자기 종가 포함 수집)에서, us 층은 ①과 별개로 `index_daily` 포워드(⑤)에서 유도 가능. 유도 뷰로 대체되면 이름은 `s3_etf_profile`·마스터가 정본(이미 그 방향 — layers.py) | 중 — 소비처가 12+ 파일이라 뷰 호환(같은 스키마 symbol·kind·date·close·name)으로 대체해야 한다 |
| ④ | **FX·해외지수·금리 일봉 포워드** (`fx_daily`·`index_daily`·`rates_daily`) | 2026-07-31 이후 공백. 거시 계열 방아쇠(`paneltest.macro_z`)의 입력 — 공백이면 방아쇠가 침묵 | 하 — FMP 일봉 수집기(`ingest-price-raw`)와 동형, 심볼만 다르다. FMP 공용키 한도(ALPHA-558) 재확인 필요 |
| ⑤ | **투자자 수급 롱포맷·프로그램매매 포워드** (`investor_value_daily`·`program_trading_daily`) | 수급 계열 방아쇠(`flow_z`) 입력, 2026-07-31 이후 공백. 단 EOD 수급(`investor_flow_daily`)이 상시라 **부분 중복** — 13유형 롱포맷·프로그램매매만 진짜 공백 | 하 — KIS 기존 세트 재사용 |
| ⑥ | **애널리스트 목표주가·등급 포워드** (`analyst_target`·`rating_dist`) | 공백 중이나 소비 어휘가 아직 얇다 | 하 |
| ⑦ | **`tau_sidecar` 폐지 경로** — 기존 canonical·RDB 행의 τ 재적재 운영 | 사이드카는 "재적재 전까지의 다리"로 설계됐다(모듈 docstring). 신규 행은 이미 초 단위(1분 레인·parse 수술) — 과거분 재정제가 남은 일 | 중 — 수집이 아니라 재적재 운영 |
| ⑧ | **DataGuide curated 갱신 체계** (`pit_daily`·`fin_annual`·`flow_daily`·`dg_*` 의 상류) | 갱신이 사람 손 — 주기·주체·라이선스 미정. 결정 전에는 전환 불가 | 별도 결정 필요 |

## 4. 수집 파이프라인 스펙 (①·② 상세 — 스펙까지만)

### 4.1 KRX 업종지수 일봉 (`sector_index_daily`) + 업종 분류 (`sector_membership`)

- **원천 API**: pykrx (KRX 정보데이터시스템 비공식 래퍼) — `get_index_ohlcv`(업종지수 일봉)·
  `get_market_sector_classifications(날짜, 시장)`(PIT 분류). `krxsector.py` 로 검증된 경로다.
  - 대안 검토: KRX 정보데이터시스템 직접 호출은 **불가**(로그인 게이트 + IP 차단 이력 —
    open-source-backfill.md §2 기록, bld 브루트포스 금지). KRX Open API(AUTH_KEY,
    `krx_instrument` 세트)에 업종지수 엔드포인트가 있는지 [실측 필요] — 있으면 pykrx 보다
    안정적(pykrx 는 스크레이핑 기반이라 KRX 개편에 취약).
  - **KIS 업종 일봉 TR** 존재 시 그쪽이 최선(자격증명·레이트리밋 체계 기존 재사용) [실측 필요].
- **인증·레이트리밋**: pykrx 는 무인증·비공식(과호출 시 차단 리스크 — 저부하 직렬 + 지수
  45종/일 1콜 수준이라 실질 위험 낮음). KIS 경로면 기존 앱키·토큰 공유 캐시(ALPHA-573) 재사용.
- **주기·트리거**: 일봉 — 시장 레인(etf-daily SFN) raw 페이즈에 잡 1개 추가, 장 마감 후
  (15:40 KST 슬롯과 동일). 분류 스냅샷 — 분기 1회면 충분하나 스케줄 어휘를 늘리지 않게
  **매일 받아 canonical 이 as_of 로 접는** 쪽을 권고(응답이 작다).
- **존·경로** (storage.py 에 빌더 신설):
  - raw: `raw/source=krx/dataset=sector_index_daily/market=KR/ingest_date=…/run_id=…`
    (bronze 통일 규약 동형 — 응답이 여러 거래일을 줄 수 있어 ingest_date 파티션).
  - canonical: `canonical/market_data/sector_index_daily/market=KR/trade_date=…`
    (price_daily 동형 — 행 키 code). 분류는
    `canonical/reference/sector_membership/market=KR/as_of_date=…`
    (instrument_profile 동형 — 참조 데이터라 reference 존).
  - duck 의 `sector_index`·`sector_member` 백필 세트는 이 canonical 직독 뷰로 대체 후 폐지
    (스키마 호환: trade_date·code·close / as_of·ticker·code·market 유지).
- **소급 백필**: pykrx 가 과거 구간을 주므로 2026-08-04~현재 공백은 같은 코드로 1회 소급.
  백필 격리 좌표(run_id=`backfill-…`) 준수.
- **완전성 판정**: collection_log ops 봉투. 기대 집합 = 분류가 실제 가리키는 업종지수 코드
  (krxsector.py 의 used 집합과 같은 유도 — 고정 45 하드코딩 금지). 거래일인데 0행이면
  INCOMPLETE. `ALIAS`(구 분류명 흡수)·unmapped 카운트를 quality_log 에 남긴다.
- **실패 시 관측**: ops 카탈로그 등록(시장 레인) — MISSED/LEDGER_GAP 원장 판정 + 기존
  raw 실패 SNS 알림 재사용.
- **의존 순서**: 독립 — 즉시 착수 가능. ③(layers_daily 대체)의 일봉 섹터 축이 이것에 의존.

### 4.2 KRX 업종지수 분봉

- ✅ **선결 확인 종료 — 실재한다**(ALPHA-887, 2026-08-08~09 실측). TR 은
  `FHKUP03500200`(`kis_sector_index.py`)이고 시장구분은 `U` 다. 아래 "실재 시" 갈래로
  갔다 — 어댑터(#645)와 1분 레인 배선(sector_index_minute dataset)이 모두 섰다.
  - 봉만 받는 축(`sector_etf_ids` 와 같은 "판정 밖 참조 계열" 규약, ALPHA-842)으로 45종을
    편입했다. 어댑터는 `kis_minute` 를 상속해 재시도·토큰·실패 축을 공유한다. 존은
    `canonical/market_data/sector_index_minute/…` (price_minute 동형, 결정적·불변 artifact
    키 + generation). 처리량: 45콜/분 추가 — KIS 실측 14.8req/s 예산에서 3초.
  - 🔴 **KIS 지수코드는 KRX 업종코드가 아니다.** `U` 네임스페이스는 자체 조밀 번호라
    산술 관계가 없고, KRX 코드를 그대로 넣으면 `rt_cd=0` 에 **남의 지수**가 온다. 그래서
    수집 대상 정본이 목록이 아니라 **맵**이다(`[minute_sector_index.index_map]` 45줄,
    일봉 종가와 99거래일 전건 대조로 확정). 번역은 어댑터 안에서 끝난다 — canonical 의
    `unit_id` 는 KRX 업종코드다(아니면 일봉 `sector_index` 와 조인이 안 된다).
  - ⭐ **라벨이 구간의 시작이다** — 주식 당일 TR(구간 **끝**)과 **반대 축**이다.
  - 🔴 **소급은 불가하다.** 소급 TR 은 일봉으로 degrade 한다 — 놓친 분은 영구 결손이라
    이 dataset 은 복구 예산이 0 이고 오늘이 아닌 `--session-date` 를 기동에서 거부한다.
  - ⛔ 부재 시 갈래(섹터 ETF 대체를 확정 설계로 명문화)는 **채택되지 않았다**. 다만 대체
    자체는 남는다 — 소비 표면 전환(③)은 아직이다.
- **주기·트리거·완전성**: 1분 레인 세션 원장(window·manifest) 계약을 그대로 받는다 —
  별도 완전성 장치 불필요. 단 **기대 집합이 universe 가 아니라 config** 라
  (`UNIVERSE_DATASETS` 밖) 그 정체성을 세션에 따로 고정한다(`config_set_identity`).
- **의존 순서**: ① 과 독립이나, 소비(장중 섹터층 전환)는 ③ 의 설계 확정 이후.

### 4.3 나머지 (④~⑥) 공통 스펙

- **FX·지수·금리(④)**: FMP stable `historical-price-eod/full`·`treasury-rates`. 인증 =
  기존 FMP 키(Secrets `edge-dev-data-pipeline/fmp/api-key`) — **공용키 한도 소진으로 US
  수집 토글이 꺼져 있는 상태**(ALPHA-558)라, 이 소량(심볼 십수 개) 수집이 한도에 드는지
  먼저 확인. 존은 기존 §1.2 경로 유지, storage.py 빌더 신설. `change_pct` 재계산(LAG)·
  `DXY=DX-Y.NYB`·`^SOX→SOXX/SMH` 함정은 open-source-backfill.md §1 이 정본.
- **수급 롱포맷·프로그램매매(⑤)**: KIS — 기존 `kis_investor` 세트·유니버스 파생 재사용.
  백만원→원 환산(1e6)·차익/비차익 NULL 유지. 존은 기존 경로 유지.
- **④·⑤·⑥ 모두**: 시장 레인 raw 페이즈 잡 추가 + canonical 정제 스텝 + collection_log
  봉투 + ops 카탈로그 등록이 한 묶음이다(계측 없는 스텝을 만들지 않는다 — ALPHA-610 이후
  `instrumented=False` 0개 규율 유지).

## 5. 남는 결정 (이 스펙이 정하지 않은 것)

- DataGuide curated 의 갱신 주체·주기·라이선스 (§3-⑧) — 데이터 소스 라이선스 스냅샷은
  [domain/data-source-licensing.md](../domain/data-source-licensing.md) 참고.
- `pit_daily` 류 접기(fold) 산출물을 언제 재생성하는가 — curated 갱신 체계에 종속.
- draft/canonical(dartlab) 7셋의 승격 여부 — PIT 아님이 확인된 입력이라 승격 전에 정정
  이력 소스(list.json + document.xml 파싱) 결정이 선행.
