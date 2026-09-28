# 레이크 경로 전환 대장

상태: 제안 · 2026-09-28. 결정은 [ADR-0057](../adr/0057-lake-dataset-canonical-consumption-and-retirement.md)(제안됨)이고,
설계 기준은 [ETF 데이터 저장 경로 설계 초안](etf-data-storage-plan.md) §7·§8이다. 이 문서는 데이터셋별 현재 경로·
writer·reader·목적 표면·전환 상태의 대장이다. 팀 승인 완료를 뜻하지 않는다.

**실측 조건**: dev 계정 `393229433969`(ap-northeast-2), 2026-09-28 KST 11~13시.
S3 `list-objects-v2`·Glue `get-tables`·DuckDB 읽기로 쟀다. 코드 위치는 같은 날 `dev`(388177ba) 기준이다.
적힌 수치는 이 시점의 관측값이며 계약이 아니다. **재지 않은 것은 `미확인`으로 적었다.**
옛 문서의 수치는 옮기지 않았다.

상태 열은 ADR-0057 §6의 넷을 따로 적는다: **코드** / **이관** / **소비 전환** / **폐기**.

⚠️ §3의 두 도구(`migrate_fmp_5min_raw.py`, `backfill_intraday_5m.py --vendor fmp`)는 [#952](https://github.com/alphaeveryday/edge/pull/952)(ALPHA-1104)가 들여온다.
그 PR이 머지되기 전의 `dev`에는 이 인자들이 없다. 적힌 dry-run·이관은 그 브랜치 코드로 실행한 결과다.

## 1. 대장

| 데이터셋 | 현재 경로·포맷 | 객체·용량 | 기간 (최신) | 행 키·단위·시각 | writer | 주요 reader | 목적 표면 | 상태 |
|---|---|---|---|---|---|---|---|---|
| `fx_usdkrw` | `analysis/backfill/fx_usdkrw.parquet` | 1 · 8KB | 2025-06-29~**2026-07-31** · 342행 | `date`(VARCHAR, 주말 포함) · FMP USDKRW OHLC · `change_pct`=**일중** (close−open)/open×100(342행 전부) | `statics/backfill.py`(수동 CLI, 로컬 출력). S3 업로드 주체는 레포 밖 | `paneltest.py:235`(`fx_beta` 회귀 변수) | `canonical/market_data/fx_daily` 호환 뷰 | §2 — 전환 조건 미충족 |
| `us_market` | `analysis/backfill/us_market.parquet` | 1 · 7KB | 2025-06-30~**2026-07-31** · 274행 | FMP SPY OHLC · `change_pct` 251/274행만 일중 정의와 일치 | 위와 같음 | SQL 소비자 0. `sqltool.WHITELIST`로 LLM SQL 도구에 노출 | `canonical/market_data/index_daily` SPY 호환 뷰 | §2 |
| `fx_daily` | `canonical/market_data/fx_daily/market=GLOBAL/trade_date=…/part-{0,1}.parquet` | 735 · 0.87MB | 2025-06-01~**2026-07-31** · 4계열×366 | DXY·EURUSD·USDJPY·USDKRW · `change_pct`=**전일 대비**(366행 전부) · `available_at`=다음날 06:00 KST | **레포 안 writer 없음**(2026-08-02 레포 밖 일회성) · storage.py 빌더 없음 | `paneltest._mz`·`macro_z` | 유지(생산자 신설 — ALPHA-1105) | 공백 중 |
| `index_daily` | `canonical/market_data/index_daily/market=US/…` | 582 · 0.71MB | 2025-06-02~**2026-07-31** · 6계열×293 | QQQ·SMH·SOXX·SPY·^GSPC·^NDX · 비조정 종가 | 위와 같음 | `paneltest._mz`·`surface.py:301` | 유지(ALPHA-1105) | 공백 중 |
| `rates_daily` | `canonical/market_data/rates_daily/market=US/…` | 292 · 0.54MB | ~2026-07-31 (미확인: 계열별 말일) | 미확인 | 위와 같음 | `paneltest.py:125,139` | 유지(ALPHA-1105 범위로 제안) | 공백 중 |
| 5분봉 KR 애드혹 raw | `raw/kr_intraday/fmp_5min/*.K[SQ].parquet` (+cursor·log·스크립트·`kospi200_proxy.parquet`) | 2,544 · 656MB (데이터 1,271) | 2022-11~2026-07-16 | `symbol`(`.KS`/`.KQ`)·`datetime`(KST naive, 구간 시작)·OHLCV | 레포 밖 수집기(파일에 `fetch_fmp_5min.py` 동봉) | `duck.s3_kr_5min`(`*.KS`만, 코드 소비자 0) | 표준 raw `raw/source=fmp/dataset=price_5min/market=KR/…` | §3 — 이관 완료 |
| 5분봉 KR gap raw | `raw/kr_intraday/fmp_5min_gap/gap*.parquet` | 11 · 8.4MB | 2026-06-01~07-31 | 위와 같음, 파일당 여러 종목 | **`collect/intraday.py:publish_raw`(활성)** | 없음(레포 밖 `normalize_intraday.py`가 읽는다고 주석에 적혀 있다) | 표준 raw | §3 — 이관 완료 · writer 잔존 |
| 5분봉 US raw | `raw/fmp_5min_us/*.parquet` (+`*.partNNNN.parquet`) | 2,127 · 8.35GB (데이터 2,100) | 미확인 (500,047,949행 · 2,016종) | `ts`=ET naive · 정규장 밖 봉 포함(추정 — canonical 행 수와 차이) | 레포 밖 | `duck.s3_us_5min`(코드 소비자 0) | 표준 raw | §3 — 이관 완료 |
| 5분봉 US gap raw | `raw/fmp_5min_us_gap/gap_*.parquet` | 6 · 50MB | 2026-06-22~07-25 주차 | 위와 같음 | `collect/intraday.py:publish_raw`(활성) | 없음 | 표준 raw | §3 — 이관 완료 · writer 잔존 |
| 5분봉 canonical | `canonical/market_data/intraday_5m/market={KR,US}/trade_date=…/part-*.parquet` | KR 988 파티션 · US 647 · US 1.14GB | KR 2022-11-01~2026-09-28 · US 2024-01-02~**2026-07-31** | (market, ticker, ts) · `ts`=구간 시작 · `available_at`=ts+5분 · KR 파일 `part-0`(롤업/fmp) · `part-kis-backfill`·`part-toss-backfill`·`part-sector-index` | 롤업(`minute/rollup.py`, 경계 `WRITER_SINCE`=2026-08-10) · `backfill_intraday_5m.py` · **`collect/intraday.py:publish_canonical`(가드 없음, ALPHA-1106)** | `duck._bars`(Iceberg 폴백 시) · `s3_intraday_5m` · Glue `edge_intraday_5m_src`(외부 표) | 5분봉 운영 조회 정본 | §3 |
| Glue 5분봉 표 | `market_data_kr.edge_intraday_5m` (Iceberg, **버킷 `market-data-393229433969`**) | 메타 00041 · 2026-08-05 갱신 | fmp 63,889,777 · fmp_backfill 563,249 · 1m_rollup 16,652(duck 주석, 08-07 실측) | canonical과 같은 열 + `symbol` | **레포 밖**(ALPHA-796), 08-05 이후 정지 | `duck._bars_iceberg`(기본 1순위) | 대체 대상(canonical로 수렴) | §3.4 |
| 로컬 5분봉 이력 | 노트북 `.tmp/causal-backfill/bars/*.parquet`(이 기기에 없음) → 스테이징 `s3://market-data-393229433969/KR/_staging/local_5m/part-0.parquet` | 1 · 9MB | 2022-11-09~2026-08-05 · 734,633행 · 16종 | `symbol`·`ticker`·`ts`(VARCHAR)·`available_at`=ts+5분 · `source_vendor=fmp_backfill` | 레포 밖(`backfill.py merge_bars`가 로컬에 쓰던 형식) | Glue `edge_intraday_5m_local` → Iceberg 적재. `duck._bars` 로컬 가지 | canonical `part-fmp-backfill.parquet` | §3.3 — dry-run만 |
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

## 3. 5분봉 — 임시 raw·로컬 이력의 수렴

### 3.1 raw 이관 (완료 — 2026-09-28)

도구: `data-pipeline/scripts/migrate_fmp_5min_raw.py`(기존 `migrate_kr_intraday_5min.py`를 확장, ALPHA-1104).
server-side copy만 하므로 값 변형이 없다.

| preset | 원천 | 대상·제외 | 목적 (`raw/source=fmp/dataset=price_5min/…`) | 결과 |
|---|---|---|---|---|
| kr | `raw/kr_intraday/fmp_5min/` | 1,271 · 1,273(cursor 1,269·log·스크립트·pyc·`kospi200_proxy`) | `market=KR/ingest_date=2026-07-29/run_id=run_8645481c2c1d4451af227c1633f1030d/` | **2026-07-29 기존 실행** · 오늘 재검증 1,271 `already_identical` |
| kr-gap | `raw/kr_intraday/fmp_5min_gap/` | 11 · 0 | `market=KR/ingest_date={2026-08-02,08-04}/run_id=run_4282c8a71d6479b2633aab83671785cf/` | 복사 11 |
| us-gap | `raw/fmp_5min_us_gap/` | 6 · 0 | `market=US/ingest_date=2026-08-02/run_id=run_1ae43d9dfd62c4f2b41818a368f19208/` | 복사 6 |
| us | `raw/fmp_5min_us/` | 2,100 · 27(cursor 24·log·out·스크립트) | `market=US/ingest_date=2026-07-25/run_id=run_39e89ffcdce8500e9d8507b051751c4e/` | 복사 2,100 (멀티파트 원천 42개는 MD5로 대조) |

- 검증: 단일 파트는 ETag(SSE-S3, MD5)가 같고, 멀티파트는 원천 MD5와 목적 ETag가 같다. 재실행하면 전건
  `already_identical`로 복사가 0이다. 뷰 행 수도 신구가 같다(KR `*.KS` 47,117,315 · US 500,047,949).
- 이관 기록: `operations_archive/collection_logs/source=fmp/dataset=price_5min/started_date=2026-09-28/run_id=<run_id>-<시각>/log.json`.
  원천 키·ETag·크기·LastModified·목적 키·목적 ETag·상태가 객체마다 한 줄이다. 버킷 버전 관리가 꺼져 있어
  `src_version_id`는 null이다.
- ⚠️ KR 기존 복사(07-29)의 `ingest_date`는 **복사일**이다(원천 LastModified는 07-25). 새 도구는 원천 날짜를 쓴다.
  기존 복사본은 바이트가 같으므로 다시 쓰지 않고 이 차이만 기록한다.
- 이관하지 않은 것: 수집 상태 파일(cursor·log·스크립트)과 `kospi200_proxy.parquet`(가격 raw가 아닌 종목 참조)은
  구 프리픽스에 남긴다. `raw/kr_intraday/fmp_1min/`(2,539 · 2.05GB, 1분봉)은 대상 dataset이 정해지지 않아
  **미이관**이다.

### 3.2 raw → canonical 대조 (재정제 여부)

| | 고유 키 (ticker, ts) | canonical 쪽 | 결과 |
|---|---|---|---|
| KR (본체 63,041,970 + gap 850,785 행) | 63,889,777 (본체·gap 겹침 키 값 충돌 0) | fmp `part-0` 63,824,894 | 63,824,894 키 **값 차이 0** · canonical에만 있는 키 0 · **raw에만 64,883** |
| US (본체 500,047,949 + gap 4,197,527 행) | 미확인 | 87,615,393 (2,015종, 2024-01-02~2026-07-31, 전부 fmp) | 키 단위 대조 **미확인**. 행 수 차이는 기간·정규장 절단으로 추정 |

- KR의 raw 전용 64,883 키는 2022-11-10~21 7일(+2024-01-15 1행)에 몰려 있다. 그 파티션을 포함한 KR `part-0`
  258개가 **2026-08-09 10:3x UTC에 일괄 재작성**됐다. 주체와 의도는 미확인이다([ALPHA-1106](https://alphaeveryday.atlassian.net/browse/ALPHA-1106)).
  같은 키를 Glue 표는 가지고 있다(Glue fmp 63,889,777 = raw 고유 키 수).

### 3.3 로컬 이력 → canonical (코드 완료 · dry-run만 · **미실행**)

도구: `backfill_intraday_5m.py --vendor fmp`(기존 벤더 축 확장, #952 이후). 원천은 스테이징 parquet 하나다.
목적은 파티션별 `part-fmp-backfill.parquet`(추가 전용, `source_vendor=fmp_backfill`)이다.

- **보존 규칙**: `writer_owns` 소유일(2026-08-03, ≥2026-08-10)에는 쓰지 않는다. 그날 정본·다른 벤더가 가진
  (종목, 날짜)는 원천 값이 달라도 건너뛴다(운영이 지금 읽는 값을 바꾸지 않는다). 벤더 봉 시각(15:30 종가 봉 포함)·
  `symbol`(접미사 포함 → `source_symbol`)·원천 `available_at`을 그대로 옮긴다. `available_at ≠ ts+5분`인 원천은
  거부한다.
- **dry-run (2026-09-28 12:42~12:47 KST)**: 원천 734,633행 = 착지 예정 **543,290**(901일·16종) + 이미 가진 칸
  190,322 + 달력 밖 1,021. 원천 sha256·ETag·날짜별 목적 키가 `--report` JSON에 남는다.
  - 이미 가진 칸 중 원천 값과 같은 것: fmp `part-0` 동일 시각 170,966행(차이는 volume 2행). 다른 것: `toss_backfill`
    18,022행 중 close 9,997행이 다르고, `1m_rollup` 1,487행 중 close 1,150행이 다르다. 이 칸은 현재 값을 유지한다.
    원천 우선으로 바꿀지는 데이터 품질 결정으로 남긴다.
  - 예전 노트북 합집합 규칙(`duck._overlap_cuts`: `ROLLUP_FROM` 이전에는 로컬 우선)과 **우선순위가 다르다.**
    운영 컨테이너는 로컬 가지가 없어 그 규칙을 쓴 적이 없다. 따라서 이 이관은 운영 규칙(먼저 착지한 쪽 우선)을 따른다.
- **실행 계획(승인 대기)**: 대상은 dev `393229433969` · `edge-dev-pipeline-lake` · KR 901 파티션이다.
  분석엔진이 읽는 파티션이 바뀌므로 **장 마감 뒤**(15:40 시장 런 이후)에 돌린다.
  ```bash
  AWS_PROFILE=edge uv run python scripts/backfill_intraday_5m.py --vendor fmp --days 5000 \
    --source-parquet s3://market-data-393229433969/KR/_staging/local_5m/part-0.parquet --report fmp-run.json
  ```
  원천 행이 전부 설명되지 않으면 exit 1이다. 이관 대장은
  `operations_archive/collection_logs/source=fmp/dataset=intraday_5m/…/log.json`에 남는다.
  중단되면 그대로 다시 돌린다(이미 쓴 칸은 `covered`로 걸러진다).
- **실행 후 검증**: ① 16종의 (ticker, ts) 집합을 Glue 표(fmp_backfill)와 canonical에서 비교한다. 차이는 위
  "이미 가진 칸"과 달력 밖 행으로 전부 설명돼야 한다. ② `CausalLake(day=…)` 폴백 경로의 `bars_5m`에서 069500·
  섹터 ETF 거래일 수를 이관 전후로 비교한다(늘기만 해야 한다). ③ 롤업 foreign 가드가 걸리지 않았는지 본다
  (소유일에는 쓰지 않으므로 0이어야 한다).
- **이관 중 신규 유입**: 대상 파티션은 전부 롤업 소유 경계 이전이라 새로 들어오는 봉이 없다. 기준점은 원천
  스냅샷이다. dry-run 시점 값은 ETag `227ed4bd52f9a28e6063a86417418b00`, sha256 `e241a402…8bb7fa2`, 734,633행이다.
  실행 보고서의 값이 이와 다르면 원천이 바뀐 것이다. 그 경우 dry-run부터 다시 한다. 재실행은 빈 칸만 더한다.
- **롤백**: 보고서 `landed[].dest_key`의 `part-fmp-backfill.parquet`만 지운다. 이 파일명은 이관 전에는 레이크에
  하나도 없었다(2026-09-28 목록 확인). 따라서 삭제하면 이전 상태로 정확히 돌아간다.

### 3.4 소비 경로

| 소비 경로 | 지금 | 목표 | 상태 |
|---|---|---|---|
| `duck.s3_kr_5min`·`s3_us_5min` | 애드혹 프리픽스 | 표준 raw 이관 스냅샷 (같은 바이트) | 코드 완료(ALPHA-1104 PR) · 배포 전 |
| `duck._bars` (`bars_5m`) | Glue 표 우선, 신선도 미달이면 canonical 합집합 + 로컬 가지 | canonical 단일 경로 | **미착수** — §3.3 실행·검증 뒤 별도 PR |
| `collect/intraday.py` | 구 gap 프리픽스와 canonical `part-0`에 직접 쓴다 | 표준 raw · 소유권 가드 | ALPHA-1106 |

- `CausalLake` 생성 지점 15곳 중 `day=`를 주는 곳은 `pipeline.py`·`window_batch.py` 둘뿐이다. 나머지 13곳(CLI·실험)은
  `iceberg_covers`가 최신일 유무만 본다. 그래서 08-05에 멈춘 Glue 표를 계속 정본으로 쓴다.
  Glue 표 기본값을 끄는 전환이 이 13곳의 결과를 바꾸는 이유다. 그 전에 §3.3이 선행돼야 하는 이유는
  그 표에만 있는 이력 때문이다.
- 운영 소비 전환 뒤에도 Glue 표·스테이징 파일·애드혹 raw는 남긴다. 폐기 조건은 §5다.

## 4. 후속 데이터셋

### 4.1 `sector_index`·`sector_member`

목적 표면(`sector_index_daily`·`sector_membership`)의 생산자가 없다. 스펙은 [통일 스펙](data-source-unification-spec.md)
§4.1에 있다. 1분 레인의 `sector_index_minute`는 다른 데이터셋이다(분봉, 소급 불가). 일봉 대체로 쓰지 않는다.
2026-08-03 이후 일봉 공백이 매일 자란다.

### 4.2 `layers_daily` — 축별 실측

| kind | 동일 의미 데이터 | 판정 |
|---|---|---|
| us (IXIC·GSPC·VIX·SOX·SOXX·SMH, 939일) | `index_daily`는 QQQ·SMH·SOXX·SPY·^GSPC·^NDX, 293일 | **대체 불가**. 겹치는 SOXX·SMH도 종가가 293일 중 290·285일 다르다(최대 1.55, 과거로 갈수록 벌어진다 → 조정 종가로 추정). GSPC↔^GSPC는 최대 0.01(정밀도). IXIC·VIX·SOX는 없다 |
| market (069500, 897일) | `canonical/market_data/price_daily` KR은 2025-07-17~ 291일 | 기간 부족 — 이력 소급 전 대체 불가 |
| sector (ETF 80) · stock (856) | price_daily 동일 | 기간 부족 · 가격 정의(수정주가 여부) 미확인 |

섹터 ETF를 KRX 업종지수로 바꾸는 것은 분석 의미 변경이라 이 전환에 넣지 않는다.

### 4.3 DataGuide·draft Iceberg

- DataGuide는 공급 계약·갱신 담당이 없어 승격 조건 1을 못 채운다. `pit_daily`·`fin_annual`·`flow_daily`는
  대체 생산 경로가 서기 전까지 유지한다.
- draft Iceberg 20표 중 데이터가 있는 것은 `statement_line`·`report_current` 둘이다. 이 둘은 writer
  (`canonical/run.py`)가 레포에 있지만 스케줄이 없다. 나머지 18표는 메타데이터만 있다.
  승격은 표별로 writer 스케줄과 Dataset Contract를 붙이고, Glue DB(`edge_lake_draft`→`edge_lake`)와 메타데이터를
  옮긴 뒤 실제 조회로 확인하는 방식이다. prefix 복사로는 승격하지 않는다.
- Glue `market_data_kr.dg_market_daily_*`와 `market-data-393229433969` 버킷의 나머지 표(ff5·뉴스·가격 등)는
  레포 참조가 0이고 조사 범위 밖이다(미확인).

## 5. 구 경로 — 남은 소비자와 폐기 조건

| 구 경로 | 남은 writer | 남은 reader | 폐기 조건 |
|---|---|---|---|
| `raw/kr_intraday/fmp_5min/`·`raw/fmp_5min_us/` | 없음(레포 밖 수집기 정지) | ALPHA-1104 배포 전까지 `duck.s3_kr_5min`·`s3_us_5min` | 배포 후 reader 0 확인 + 보존 기간 결정 + 승인 |
| `raw/kr_intraday/fmp_5min_gap/`·`raw/fmp_5min_us_gap/` | `collect/intraday.py:publish_raw` | 레포 밖 `normalize_intraday.py`(주석) | ALPHA-1106 writer 이전 후 |
| `raw/kr_intraday/fmp_1min/` | 없음 | 없음(레포 참조 0) | dataset 결정 전 보존 |
| Glue `edge_intraday_5m`·스테이징 `local_5m` | 레포 밖(정지) | `duck._bars_iceberg`(기본 1순위) | §3.3 이관 + `bars_5m` canonical 전환 + 13 생성 지점 결과 대조 후 |
| `analysis/backfill/{fx_usdkrw,us_market}` | `statics/backfill.py`(로컬) | `paneltest`·`sqltool` | ALPHA-1105 생산 + 호환 뷰 전환 후 |

하드코딩 부채: `pit.py:21`·`fin.py:21`·`flowhist.py:20`·`dgwide.py:39`(→`tool_fin.py:69`)·`tau_sidecar.py:32`가
`edge-dev-pipeline-lake`를 박아 쓴다. `duck.py:47`은 Glue 카탈로그 계정 `393229433969`를 박아 쓴다.
신규 코드에는 금지하고(ADR-0057 §3), 기존 것은 각 세트를 전환할 때 env로 옮긴다.
