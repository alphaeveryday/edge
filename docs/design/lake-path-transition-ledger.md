# 레이크 경로 현황 대장

상태: 현황 기록 · 2026-09-28 · **레이크 정리는 축소 범위로 완료**(§8). **경로 통합·기존 데이터 이관·소비자 전환은 보류했고 완료가 아니다**(§7 후속).
관련 결정은 [ADR-0057](../adr/0057-lake-dataset-canonical-consumption-and-retirement.md)(**제안됨** — 승인·구현 아님)이다.
신규 수집 데이터의 저장 계약은 [ETF 데이터 저장 경로 설계 초안](etf-data-storage-plan.md) §9에 있다.
이 문서는 기존 데이터셋별 경로·생산자·소비자·확인된 문제의 기록이다. 팀 승인을 뜻하지 않는다.

**정보의 출처를 가른다.**
- **[실측]**: dev 계정 `393229433969`(ap-northeast-2), 2026-09-28 KST 11~15시. S3 `list-objects-v2`·Glue `get-tables`·
  DuckDB 읽기로 쟀다. 표의 객체·용량·기간·행 수와 §2·§3의 대조가 여기 해당한다. 관측값이지 계약이 아니다.
- **[코드]**: 같은 날 `dev`(388177ba) 코드·terraform 조사. 표의 writer·reader가 여기 해당한다.
  런타임에 실제로 그 경로가 돌았는지는 따로 적힌 것만 확인했다.
- **[문서]**: 기존 문서·티켓의 기록이다. 옮길 때는 출처를 적었고, 옛 문서의 수치를 현재 사실로 쓰지 않았다.
- 재지 않은 것은 `미확인`으로 적었다.

## 1. 대장

| 데이터셋 | 현재 경로·포맷 | 객체·용량 [실측] | 기간 (최신) [실측] | 행 키·단위·시각 [실측·코드] | writer [코드] | 주요 reader [코드] | 목적 표면 (제안) | 상태 |
|---|---|---|---|---|---|---|---|---|
| `fx_usdkrw` | `analysis/backfill/fx_usdkrw.parquet` | 1 · 8KB | 2025-06-29~**2026-07-31** · 342행 | `date`(VARCHAR, 주말 포함) · FMP USDKRW OHLC · `change_pct`=**일중** (close−open)/open×100(342행 전부) | `statics/backfill.py`(수동 CLI, 로컬 출력). S3 업로드 주체는 레포 밖 | `paneltest.py:235`(`fx_beta` 회귀 변수) | `canonical/market_data/fx_daily` 호환 뷰 | 전환 보류 (§2) |
| `us_market` | `analysis/backfill/us_market.parquet` | 1 · 7KB | 2025-06-30~**2026-07-31** · 274행 | FMP SPY OHLC · `change_pct` 251/274행만 일중 정의와 일치 | 위와 같음 | SQL 소비자 0. `sqltool.WHITELIST`로 LLM SQL 도구에 노출 | `canonical/market_data/index_daily` SPY 호환 뷰 | §2 |
| `fx_daily` | `canonical/market_data/fx_daily/market=GLOBAL/trade_date=…/part-{0,1}.parquet` | 735 · 0.87MB | 2025-06-01~**2026-07-31** · 4계열×366 | DXY·EURUSD·USDJPY·USDKRW · `change_pct`=**전일 대비**(366행 전부) · `available_at`=다음날 06:00 KST | **레포 안 writer 없음**(2026-08-02 레포 밖 일회성) · storage.py 빌더 없음 | `paneltest._mz`·`macro_z` | 유지(생산자 신설 — ALPHA-1105) | 공백 중 |
| `index_daily` | `canonical/market_data/index_daily/market=US/…` | 582 · 0.71MB | 2025-06-02~**2026-07-31** · 6계열×293 | QQQ·SMH·SOXX·SPY·^GSPC·^NDX · 비조정 종가 | 위와 같음 | `paneltest._mz`·`surface.py:301` | 유지(ALPHA-1105) | 공백 중 |
| `rates_daily` | `canonical/market_data/rates_daily/market=US/…` | 292 · 0.54MB | 2025-06-02~**2026-07-31** · 292일 | `source_vendor=fmp` · 계열 구성·단위는 미확인([문서] open-source-backfill: 미 국채 12개 만기) | 위와 같음 | `paneltest.py:125,139` | 유지(ALPHA-1105 범위로 제안) | 공백 중 |
| 5분봉 KR 애드혹 raw | `raw/kr_intraday/fmp_5min/*.K[SQ].parquet` (+cursor·log·스크립트·`kospi200_proxy.parquet`) | 2,544 · 656MB (데이터 1,271) | 2022-11~2026-07-16 | `symbol`(`.KS`/`.KQ`)·`datetime`(KST naive, 구간 시작)·OHLCV | 레포 밖 수집기(파일에 `fetch_fmp_5min.py` 동봉) | `duck.s3_kr_5min`(`*.KS`만, 코드 소비자 0) | 표준 raw `raw/source=fmp/dataset=price_5min/market=KR/…` | raw 복사 실행됨 (§3.1) · 소비 전환 보류 |
| 5분봉 KR gap raw | `raw/kr_intraday/fmp_5min_gap/gap*.parquet` | 11 · 8.4MB | 2026-06-01~07-31 | 위와 같음, 파일당 여러 종목 | **`collect/intraday.py:publish_raw`(활성)** | 없음(레포 밖 `normalize_intraday.py`가 읽는다고 주석에 적혀 있다) | 표준 raw | raw 복사 실행됨 · writer 잔존 |
| 5분봉 US raw | `raw/fmp_5min_us/*.parquet` (+`*.partNNNN.parquet`) | 2,127 · 8.35GB (데이터 2,100) | 미확인 (500,047,949행 · 2,016종) | `ts`=ET naive · 정규장 밖 봉 포함(추정 — canonical 행 수와 차이) | 레포 밖 | `duck.s3_us_5min`(코드 소비자 0) | 표준 raw | raw 복사 실행됨 (§3.1) · 소비 전환 보류 |
| 5분봉 US gap raw | `raw/fmp_5min_us_gap/gap_*.parquet` | 6 · 50MB | 2026-06-22~07-25 주차 | 위와 같음 | `collect/intraday.py:publish_raw`(활성) | 없음 | 표준 raw | raw 복사 실행됨 · writer 잔존 |
| 5분봉 canonical | `canonical/market_data/intraday_5m/market={KR,US}/trade_date=…/part-*.parquet` | KR 988 파티션 · US 647 · US 1.14GB | KR 2022-11-01~2026-09-28 · US 2024-01-02~**2026-07-31** | (market, ticker, ts) · `ts`=구간 시작 · `available_at`=ts+5분 · KR 파일 `part-0`(롤업/fmp) · `part-kis-backfill`·`part-toss-backfill`·`part-sector-index` | 롤업(`minute/rollup.py`, 경계 `WRITER_SINCE`=2026-08-10) · `backfill_intraday_5m.py` · **`collect/intraday.py:publish_canonical`(가드 없음, ALPHA-1106)** | `duck._bars`(Iceberg 폴백 시) · `s3_intraday_5m` · Glue `edge_intraday_5m_src`(외부 표) | 5분봉 운영 조회 정본 | §3 |
| Glue 5분봉 표 | `market_data_kr.edge_intraday_5m` (Iceberg, **버킷 `market-data-393229433969`**) | 메타 00041 · 2026-08-05 갱신 | fmp 63,889,777 · fmp_backfill 563,249 · 1m_rollup 16,652(duck 주석, 08-07 실측) | canonical과 같은 열 + `symbol` | **레포 밖**(ALPHA-796), 08-05 이후 정지 | `duck._bars_iceberg`(기본 1순위) | 대체 대상(canonical로 수렴) | 소비 경로 변화 없음 (§3.4) |
| 로컬 5분봉 이력 | 노트북 `.tmp/causal-backfill/bars/*.parquet`(이 기기에 없음) → 스테이징 `s3://market-data-393229433969/KR/_staging/local_5m/part-0.parquet` | 1 · 9MB | 2022-11-09~2026-08-05 · 734,633행 · 16종 | `symbol`·`ticker`·`ts`(VARCHAR)·`available_at`=ts+5분 · `source_vendor=fmp_backfill` | 레포 밖(`backfill.py merge_bars`가 로컬에 쓰던 형식) | Glue `edge_intraday_5m_local` → Iceberg 적재. `duck._bars` 로컬 가지 | canonical `part-fmp-backfill.parquet` | canonical 적재 **보류** (§3.3) |
| `sector_index` | `analysis/backfill/sector_index.parquet` | 1 · 0.2MB | 2022-11-01~**2026-08-03** · 39,220행 · 45업종 | (trade_date, code KRX 업종코드) · close | `statics/krxsector.py`(수동, KRX 로그인) | `sql_surface.v_sector_ret`·`kbeta.py` | `canonical/market_data/sector_index_daily`(신설 제안) | 미착수 |
| `sector_member` | `analysis/backfill/sector_member.parquet` | 1 · 0.09MB | as_of 2024-01-15~2026-07-15 · 11 스냅샷 · 29,964행 | (as_of, ticker, code, market) | 위와 같음 | `sql_surface.v_sector`·`attribute.py`·`kbeta.py` | `canonical/reference/sector_membership`(신설 제안) | 미착수 |
| `layers_daily` | `analysis/backfill/layers_daily.parquet` | 1 · 8MB | 2022-11-01~**2026-07-31** · 786,428행 | kind별 종목 수 stock 856 · sector 80 · market 1 · us 6 · (symbol, date) · close·volume·name | **레포 밖**(sources.toml:364) | `layers.py`·`attribute.py`·`mkttrial.py`·`trial.py`·툴 `needs` | 축별 호환 뷰(§4.2) | 대체 불가 축 확인 |
| `pit_daily`·`fin_annual`·`flow_daily` | `analysis/backfill/*.parquet` | 106MB · 2MB · 25MB | 2025-06-27~2026-07-31 · FY2000~2026 · 2022-01-03~2026-07-31 | DataGuide 파생 | `statics/pit.py`·`fin.py`·`flowhist.py`(수동, dev 버킷 하드코딩) | `sql_surface.v_pit`·`v_fin`·`paneltest` | 유지(대체 생산 전) | 유지 |
| `tau_sidecar` | `analysis/backfill/tau_sidecar.parquet` | 1 · 26MB (08-11) | published 2026-04-25~08-03 · 360,151행 | (article_id, published_kst) | `statics/tau_sidecar.py`(수동, 로컬 출력) | `trial.py`·`evidence.py`·`duck.py:1051` | 폐지 후보(τ 재적재 완료 시, 통일 스펙 ⑦) | 범위 밖 |
| `etf_holdings_fmp` | `analysis/backfill/etf_holdings_fmp.parquet` | 1 · 0.07MB | as_of 2026-01-28 하루 · 4,153행 | ETF 보유 | **레포 밖** | `layers.py:602`(`s3_etf_holdings` 0행 폴백) | 미결 | 범위 밖 |
| DataGuide curated | `draft/curated/source=dataguide/dataset={market_daily,financial_statements,investor_flow_daily,price_daily,consensus,reference,market_history}` gzip CSV | 1,337 · 2.93GB | market_daily 2025-02-03~2026-07-31 · consensus as_of ~2026-07-31 · 나머지 as_of 2026-08-02·08-05 | 롱 포맷(trade_date, ticker, item_code, value) | **레포 밖 수작업** | `duck.s3_dg_*`·`tool_dg`·`tool_consensus`·`tool_fin`·`pit/fin/flowhist/dgwide` | 승격 보류(ADR-0057 §5-1 미충족) | 유지 |
| Glue DataGuide 표 | `market_data_kr.dg_market_daily_src`(외부 표, curated 위) · `dg_market_daily_m`(Iceberg, 다른 버킷, 08-05) | 미확인 | 미확인 | — | 레포 밖 | **레포 참조 0** | 미결 | 미확인 |
| draft Iceberg | `draft/canonical/{group}/{table}` · Glue `edge_lake_draft` 20표(+`_latest` 뷰) | 177 · 22MB | 데이터가 있는 표는 `statement_line`(파일 149)·`report_current`(1)뿐, 나머지 18표는 메타데이터만 | tables.py 선언 12표 | `canonical/run.py`(Athena MERGE, 두 표만 · 스케줄 없음) | `duck.s3_statement_line`(`tool_business.py:96`) 외 7뷰(소비자 0) | 표별 승격(§4.3) | 유지 |

## 2. `fx_usdkrw`·`us_market` → 기존 `fx_daily`·`index_daily`

### 대조 결과

같은 계열인지부터 확인했다. 이름으로 가정하지 않고 값으로 확인했다.

| | 구 (백필) | 목적 (canonical) | 겹침 | 시가·종가 차이 | `change_pct` |
|---|---|---|---|---|---|
| USDKRW | 342일 (2025-06-29~) | 366일 (2025-06-01~) | 342일 (구 전부) | **0 / 0** | 정의 다름 — 구=일중, 목적=전일 대비 (최대 차 1.06%p) |
| SPY | 274일 (2025-06-30~) | 293일 (2025-06-02~) | 274일 (구 전부) | **0 / 0** | 정의 다름 (최대 차 2.60%p) |

- 계열·단위·통화는 같다. 목적 쪽이 과거 구간을 더 갖는다(USDKRW +24일, SPY +19일). 구에만 있는 날은 0이다.
- `change_pct`는 **같은 이름의 다른 값**이다. `paneltest.fx_beta`는 `fx.change_pct / 100`을 회귀 변수로 쓴다.
  따라서 입력만 바꾸면 β가 바뀐다. 전환할 때는 호환 뷰가 구 정의를 재현한다(아래).
- **양쪽 다 2026-07-31에서 멈췄다.** 레포 안에 둘의 writer가 없다. `macro_z`·`fx_beta`의 입력은 2026-08-01부터
  공백이고, 뷰는 성공한 채 행이 없다.

### 판정: 전환하지 않는다 (조건 미충족)

전환 조건은 "필요한 과거 구간 확보 + 지속 생산 경로 확보"다. 과거 구간은 확보됐다(목적 ⊇ 구). 지속 생산은
어느 쪽에도 없다. 그래서 **구 입력을 그대로 두고** 보완 작업을 [ALPHA-1105](https://alphaeveryday.atlassian.net/browse/ALPHA-1105)로
발번했다. 막힌 이유는 공급자 결정이다. FMP 공용키 한도로 US 수집 토글이 꺼져 있어(ALPHA-558) 소량 일봉 수집이
그 한도에 드는지 먼저 확인해야 한다.

생산자가 서면 기존 이름을 유지한 채 입력만 바꾼다. 호환 뷰 초안은 아래와 같다(구 스키마 `date VARCHAR, open, close, prev_close, change_pct`).

```sql
-- fx_usdkrw (us_market 은 s3_index_daily · symbol='SPY')
SELECT strftime(trade_date, '%Y-%m-%d') AS date, open, close,
       CAST(NULL AS DOUBLE) AS prev_close,             -- 구 파일도 전 행 NULL
       (close - open) / open * 100 AS change_pct        -- 구 정의(일중) 재현
FROM s3_fx_daily WHERE symbol = 'USDKRW'
```

전환 검증은 두 가지다. ① 위 뷰와 구 파일을 겹침 342일에서 대조해 open·close·change_pct 차이 0을 확인한다.
② `paneltest` 패널의 `fx_beta`를 같은 날짜 창에서 신구로 계산해 겹침 구간 차이 0을 확인한다.
단 목적이 더 가진 24일이 60일 창에 들어가는 날은 값이 달라진다. 이는 이력 추가로 생긴 차이로 따로 적는다.
`us_market` 호환 뷰의 `change_pct`는 구 파일과 23행이 어긋난다(구 값이 벤더 필드라 정의가 일정하지 않다).
SQL 소비자가 없으므로 일중 정의로 통일하고 그 차이를 전환 PR에 적는다.

## 3. 5분봉 — 임시 raw·로컬 이력

### 3.1 raw 복사 (**실행됨** — 2026-09-28, 추가만)

[실측] 애드혹 raw를 표준 raw로 server-side copy했다. 도구는 `data-pipeline/scripts/migrate_fmp_5min_raw.py`다
([#952](https://github.com/alphaeveryday/edge/pull/952), 기존 KR 복사 스크립트 확장). 원본은 그대로 두었고 삭제는 0이다.

| preset | 원천 | 목적 (`raw/source=fmp/dataset=price_5min/…`) | 결과 |
|---|---|---|---|
| kr | `raw/kr_intraday/fmp_5min/` 티커 파일 1,271 (제외 1,273: cursor·log·스크립트·pyc·`kospi200_proxy`) | `market=KR/ingest_date=2026-07-25/run_id=run_282ca79c51eb968199260508cc6cb0b3/` | 복사 1,271 |
| kr-gap | `raw/kr_intraday/fmp_5min_gap/` 11 | `market=KR/ingest_date={2026-08-02,08-04}/run_id=run_4282c8a71d6479b2633aab83671785cf/` | 복사 11 |
| us | `raw/fmp_5min_us/` 데이터 2,100 (제외 27) | `market=US/ingest_date=2026-07-25/run_id=run_39e89ffcdce8500e9d8507b051751c4e/` | 복사 2,100 |
| us-gap | `raw/fmp_5min_us_gap/` 6 | `market=US/ingest_date=2026-08-02/run_id=run_1ae43d9dfd62c4f2b41818a368f19208/` | 복사 6 |

- 검증: 단일 파트는 ETag를, 멀티파트 42개는 원천 MD5를 대조했다. 재실행 시 전건 `already_identical`이다.
  행 수는 KR `*.KS` 47,117,315, US 500,047,949로 원본과 같다.
  복사 기록은 `operations_archive/collection_logs/source=fmp/dataset=price_5min/started_date=2026-09-28/`에 남겼다.
- **이 복사본을 읽는 소비자는 없다.** 분석엔진 뷰 `s3_kr_5min`·`s3_us_5min`은 여전히 애드혹 경로를 읽는다(전환 보류).
- 중복: 2026-07-29의 기존 KR 복사(`…/ingest_date=2026-07-29/run_id=run_8645481c2c1d4451af227c1633f1030d/`, 1,271)는
  `ingest_date`에 **복사일**을 넣었다(원천 LastModified는 07-25). 그래서 원천 날짜로 다시 복사했다.
  옛 복사본은 바이트가 같은 중복으로 남아 있다. reader가 없어 운영 영향은 없고, 폐기는 §7로 넘긴다.
- 미복사: `raw/kr_intraday/fmp_1min/`(2,539 · 2.05GB)은 대상 dataset이 정해지지 않았다.

### 3.2 raw ↔ canonical 대조와 08-09 재작성 [실측]

| | 고유 키 (ticker, ts) | canonical | 결과 |
|---|---|---|---|
| KR (본체 63,041,970 + gap 850,785 행) | 63,889,777 (겹침 키 값 충돌 0) | fmp 63,824,894 | 겹침 **값 차이 0** · canonical에만 있는 키 0 · **raw에만 64,883** |
| US (본체 500,047,949 + gap 4,197,527 행) | 미확인 | 87,615,393 (2,015종, 2024-01-02~2026-07-31) | 키 단위 대조 **미확인** |

**2026-08-09 재작성의 주체를 확인했다.** KR `part-0` 258개(2022-11-10~2026-08-07)의 LastModified가 08-09 10:19~10:31 UTC다.
같은 시각 `raw/source=legacy-canonical/dataset=price_5min/market=KR/ingest_date=2026-08-09/`에 run 6개의 사본이 있다.

| run_id | 사본 | 현재 canonical과 비교 |
|---|---|---|
| `alpha-901-b1`~`b4`, `canary-20260809a` | 250 파티션의 `part-0` + `part-kis-backfill`·`part-toss-backfill` | 사본의 part-0 행 + 백필 행 = 현재 part-0 행(파티션마다 일치). 현재 그 파티션에는 백필 파일이 없다 → **백필 파일을 part-0에 병합하고 지웠다** |
| `alpha-901-invalid-fmp-20260809` | 8 파티션(2022-11-10~21 7일 + 2024-01-15) 337,320행 | 현재 part-0에 272,437행. raw에만 있던 **64,883 키 전부가 이 사본에만 있다** |

- 판정: 64,883 키는 유실이 아니다. ALPHA-901 사전 배치가 **invalid FMP로 격리**한 행이다.
  격리 판정 근거와 그 배치의 코드·실행 기록은 레포·원격 브랜치·로컬 에이전트 세션 어디에서도 **찾지 못했다(미확인)**.
- 이 판정으로 ALPHA-1106에 적었던 가설("collect.intraday의 재실행일 수 있다")은 기각한다.

### 3.3 로컬 이력 → canonical (**보류** — 적재 안 함)

[실측] 로컬 이력 스테이징 파일은 `s3://market-data-393229433969/KR/_staging/local_5m/part-0.parquet`다.
ETag `227ed4bd52f9a28e6063a86417418b00`, sha256 `e241a402…8bb7fa2`, 734,633행, 16종이다.
canonical에 없는 칸이 543,281행(901 파티션)이다. 069500과 섹터 ETF가 포함되고, 이 행은 Glue 표에만 있다.
dry-run 두 번(13:57·14:37)의 분류는 같았다.

| 분류 | 행 |
|---|---|
| 적재 후보 | 543,281 (901 파일, 2026-07 이후 날짜는 1,104행) |
| 이미 가진 칸 (정본·다른 벤더 우선, 옮기지 않음) | 190,319 |
| ↳ 같은 시각 값이 다른 행 | toss_backfill 14,812 / 18,022 · 1m_rollup 1,469 / 1,487 · fmp 2 / 170,966 (267 칸) |
| 달력 밖 (롤업 소유일 08-03 등) | 1,021 |
| 5분 격자 밖 (volume 0, 2025-01~03) | 12 |

적재를 보류한 이유(재개 전 선행 조건):
1. **대상 파티션을 다시 쓴 writer가 레포 밖에 있다.** 위 ALPHA-901 사전 배치가 백필 파일을 part-0에 병합한 전례가 있다.
   그 코드·계획이 확인되지 않은 채 적재하면, 재실행 시 새 파일도 병합·삭제될 수 있다.
2. `collect/intraday.py`의 `publish_canonical`은 `part-{i}`로 파티션 파일을 덮는다(수동 CLI, 스케줄 없음).
   → [#953](https://github.com/alphaeveryday/edge/pull/953)(ALPHA-1106) 가드가 선행이다.
3. `backfill_intraday_5m.py`(toss·kis)를 fmp 파일을 모르는 옛 코드로 돌리면 같은 칸을 두 벌 쓴다.
   fmp 벤더를 VENDORS에 둔 코드가 먼저 머지돼야 한다.
4. 롤업(1분 워커 후크·`minute-session-rollup-sector` 스케줄)은 `_rollup_day`가 `writer_owns` 밖 날짜를 거부한다[코드].
   대상 901일은 전부 소유일 밖이라 겹치지 않는다. `WRITER_SINCE`·`WRITER_OWNED_BEFORE_SINCE`를 바꾸는 배포가 없어야 한다.

이관 코드(계획 고정·파일 md5 대조·존재 검사·`--verify`·`--rollback`)와 테스트는 보존 브랜치
`feature/ALPHA-1104-5min-canonical-load-plan`에 있다. dev 머지 대상이 아니다.

### 3.4 소비 경로 [코드] — 바뀐 것 없음

| 소비 경로 | 지금 | 비고 |
|---|---|---|
| `duck._bars` (`bars_5m`) | Glue 표(08-05 정지) 우선. 요청일 착지 미달이면 canonical 합집합 | 기준일 없는 `CausalLake()` 13곳은 전부 `__main__` 연구·점검 CLI다(운영 `pipeline.py`·`window_batch.py`는 기준일을 준다). 그중 10곳은 자기 분석일·구간이 있는데 넘기지 않아 Glue를 판정 없이 정본으로 썼다 → ALPHA-1108에서 분석일로 원천을 고르고 `bars_readiness`로 드러낸다. `pit`·`fin`·`flowhist`는 5분봉을 안 읽는다 |
| `duck.s3_kr_5min`·`s3_us_5min` | 애드혹 raw | 코드 소비자 0 |
| `duck.s3_intraday_5m` | canonical 전 파일 글롭 | `part-*.parquet` 전부를 읽는다 |

## 4. 후속 데이터셋 [실측·코드]

### 4.1 `sector_index`·`sector_member`

목적 표면(`sector_index_daily`·`sector_membership`)의 생산자가 없다. 스펙은 [통일 스펙](data-source-unification-spec.md)
§4.1에 있다. 1분 레인의 `sector_index_minute`는 다른 데이터셋이다(분봉, 소급 불가).
2026-08-03 이후 일봉 공백이 매일 자란다.

### 4.2 `layers_daily` — 축별 실측

| kind | 동일 의미 데이터 | 판정 |
|---|---|---|
| us (IXIC·GSPC·VIX·SOX·SOXX·SMH, 939일) | `index_daily`는 QQQ·SMH·SOXX·SPY·^GSPC·^NDX, 293일 | **대체 불가**. SOXX·SMH도 종가가 293일 중 290·285일 다르다(최대 1.55, 조정 종가로 추정). GSPC↔^GSPC는 최대 0.01 차이다. IXIC·VIX·SOX는 없다 |
| market (069500, 897일) | `canonical/market_data/price_daily` KR은 2025-07-17~, 291일 | 기간 부족 |
| sector (ETF 80) · stock (856) | price_daily 동일 | 기간 부족 · 가격 정의(수정주가 여부) 미확인 |

섹터 ETF를 KRX 업종지수로 바꾸는 것은 분석 의미 변경이라 이 대장의 대상이 아니다.

### 4.3 DataGuide·draft Iceberg

- DataGuide는 공급 계약과 갱신 담당이 없다. `pit_daily`·`fin_annual`·`flow_daily`는 대체 생산 경로가 생기기 전까지 유지한다.
- draft Iceberg 20표 중 데이터가 있는 것은 `statement_line`·`report_current` 둘이다.
  writer(`canonical/run.py`)는 레포에 있지만 스케줄이 없다. 나머지 18표는 메타데이터만 있다.
- Glue `market_data_kr.dg_market_daily_*`와 `market-data-393229433969` 버킷의 나머지 표는 레포 참조가 0이다. 미확인이다.

## 5. 구 경로 — 남은 writer·reader [코드]

| 구 경로 | 남은 writer | 남은 reader |
|---|---|---|
| `raw/kr_intraday/fmp_5min/`·`raw/fmp_5min_us/` | 없음(레포 밖 수집기 정지) | `duck.s3_kr_5min`·`s3_us_5min` |
| `raw/kr_intraday/fmp_5min_gap/`·`raw/fmp_5min_us_gap/` | `collect/intraday.py:publish_raw`(수동) | 레포 밖 `normalize_intraday.py`(주석) |
| `raw/kr_intraday/fmp_1min/` | 없음 | 없음(레포 참조 0) |
| 표준 raw의 KR 07-29 중복 복사 | 없음 | 없음 |
| Glue `edge_intraday_5m`·스테이징 `local_5m` | 레포 밖(정지) | `duck._bars_iceberg`(기본 1순위) |
| `analysis/backfill/{fx_usdkrw,us_market}` | `statics/backfill.py`(로컬) | `paneltest`·`sqltool` |

하드코딩 부채 [코드]: `pit.py:21`·`fin.py:21`·`flowhist.py:20`·`dgwide.py:39`(→`tool_fin.py:69`)·`tau_sidecar.py:32`는
`edge-dev-pipeline-lake`를 박아 쓴다. `duck.py:47`은 Glue 카탈로그 계정을 박아 쓴다.

## 6. 확인된 문제와 미확인 사항

**확인된 문제** (결과를 틀리게 만드는 기존 결함 — 이번 작업이 만든 것 아님)

| 문제 | 근거 | 처리 |
|---|---|---|
| fx·지수·금리 canonical에 생산자가 없어 2026-08-01부터 공백이다. `macro_z`·`fx_beta`가 오류 없이 빈 입력으로 돈다 | §1·§2 [실측] | ALPHA-1105 |
| `fx_usdkrw`와 `fx_daily`의 `change_pct`는 같은 이름에 정의가 다르다 | §2 [실측] | 전환 시 호환 뷰 (ALPHA-1105) |
| `collect.intraday`가 채워진 파티션의 `part-0`을 덮는다 | [코드] (실제 발생 흔적은 없음) | #953 (ALPHA-1106) |
| 연구·점검 CLI 10곳이 분석일을 넘기지 않아 08-05에 멈춘 Glue 표를 정본으로 쓰고, 그 뒤 날짜에서 봉 0개를 오류 없이 받았다 | `iceberg_covers`는 `asked_day`가 비면 최신일 유무만 본다 [코드]. Glue 최신 2026-08-05 [실측] | ALPHA-1108 (PR 검토 중) — 원천 판정은 분석일로, 요청 구간의 봉 유무는 `bars_readiness`로 판정. 5분봉 전용 도구(interval·premium5)는 요청일 봉이 없으면 exit 2로 보류 |
| 16종 543,281행이 canonical에 없어, canonical 폴백으로 도는 운영 분석이 그 이력을 못 본다 | §3.3 [실측] | 적재 보류 (§7) |
| KR canonical 250 파티션이 레포 밖 배치로 재작성됐고, 그 코드·기록이 없다 | §3.2 [실측] | ALPHA-901에 기록 |

**미확인**
- ALPHA-901 사전 배치의 invalid-fmp 격리 판정 근거와 향후 실행 계획.
- US raw ↔ canonical 키 단위 대조.
- `rates_daily`의 계열 구성(292일, 2025-06-02~2026-07-31까지만 쟀다).
- Glue DataGuide 표와 `market-data-393229433969` 나머지 표의 생산자.
- FMP 공용키 bandwidth(rolling 30일)의 현재 잔여. 문서 기록([문서] terraform README, ALPHA-558)만 있고 재지 않았다.

## 7. 후속 작업 (이번 범위 밖)

| 작업 | 선행 조건 | 추적 |
|---|---|---|
| 로컬 이력 16종 → canonical 적재 | §3.3의 1~4. 적재 직전 dry-run을 다시 해 계획을 고정하고, 적재 후 대조, 파일 목록 기준 롤백 | ALPHA-1104 (보존 브랜치) |
| `bars_5m` Glue → canonical 소비 전환 | 위 적재·대조. 64,883 격리 키를 복구하지 않는 결정 확인. Glue 전 키 집합과 canonical 대조 | 신규 발번 필요 |
| 연구·점검 CLI의 낡은 Glue 사용 | 없음 | [ALPHA-1108](https://alphaeveryday.atlassian.net/browse/ALPHA-1108) PR 검토 중. 잔여: 운영 경로(`pipeline`·`window_batch`)는 요청일 봉이 어느 원천에도 없을 때도 계속 돈다 — `bars_readiness`를 운영 상태에 잇는 것은 별도 판단 |
| `s3_kr_5min`·`s3_us_5min`을 표준 raw로 전환 | 없음(바이트 동일). 코드는 보존 브랜치 | ALPHA-1104 |
| FX·지수·금리 생산자 | 신규 수집 저장 계약([설계 초안](etf-data-storage-plan.md) §9)을 적용해 생산자·갱신 주기·준비 판정을 정한다. FMP bandwidth 확인. 입력: FMP stable EOD(FX 4 · 지수 6 · 미 국채 금리). 산출: 기존 `canonical/market_data/{fx_daily,index_daily,rates_daily}` 경로와 파일 스키마. 주기: 거래일 1회(미국장 마감 뒤). 준비 기준: 2026-08-01~ 공백 소급과 신선도 계약(ADR-0043) | ALPHA-1105 |
| `fx_usdkrw`·`us_market` 호환 뷰 전환 | 위 생산자. `change_pct`는 일중 정의를 재현(§2) | ALPHA-1105 |
| US raw ↔ canonical 키 대조 | 없음. US 5분봉 소비 전환·US 애드혹 raw 폐기의 선행 조건 | 신규 발번 필요 |
| 구 경로 폐기 (애드혹 raw·KR 07-29 중복·Glue·스테이징) | writer 0·reader 0(코드와 런타임 모두)·보존 기간·승인 | 경로별 |
| `sector_index_daily`·`sector_membership` 생산자 | 통일 스펙 §4.1 | 미발번 |
| DataGuide·draft Iceberg 승격 | 갱신 담당·Dataset Contract | 미발번 |

## 8. 작업 상태 (축소 범위 완료 — 2026-09-28)

| 항목 | 결과 |
|---|---|
| [#953](https://github.com/alphaeveryday/edge/pull/953) `collect.intraday` 파티션 덮어쓰기 방지 (ALPHA-1106 일부) | `75c6999d` 머지 16:52:34 KST |
| [#952](https://github.com/alphaeveryday/edge/pull/952) raw 복사 도구 (ALPHA-1104 일부) | `335c8f87` 머지 16:52:44 KST |
| [#951](https://github.com/alphaeveryday/edge/pull/951) 이 대장·신규 수집 저장 계약 §9·ADR-0057 (ALPHA-901) | `29ed8678` 머지 16:53:00 KST |
| [#957](https://github.com/alphaeveryday/edge/pull/957) 기준일 없는 CLI의 낡은 Glue 사용 (ALPHA-1108) | 최종 결과는 PR·ALPHA-1108에 기록 |

- **scan**: 세 PR의 머지 시점에는 gitleaks(전체 히스토리)가 `feature/app-ui-screens`의 커밋 `a8aab422`·`ab9119ac`에서 3건을 탐지해 실패했다.
  사용자 승인으로 그 실패를 예외로 두고 머지했다(통과로 간주하지 않음).
  이후 #956(`fa2fcbc8`)이 `.gitleaksignore`에 그 3건을 사유와 함께 추가했고, 그 뒤 `dev`의 gitleaks는 통과한다.
- **배포**: 머지 시점에 ECS 상주 서비스는 전부 desired 0이었다. 재배포로 수집이 끊기지 않았다.
- **데이터**: 이 작업이 바꾼 데이터는 §3.1의 표준 raw 추가 복사(3,388개)와 그 기록 로그뿐이다. canonical·뷰·삭제는 없다.
- ADR-0057은 **제안·적용 보류** 상태다. 문서 머지는 ADR 적용이나 데이터 전환 승인이 아니다.

