# 데이터 표면 AWS 실측 및 정본 통합 계획 (ALPHA-879)

> 기준 시각: 2026-08-09 KST. AWS 계정 `393229433969`, 리전 `ap-northeast-2`,
> CLI 프로파일 `work`로 읽기 전용 조사했다. 이 문서는
> [data-source-unification-spec.md](data-source-unification-spec.md)의 런타임 실측 부록이다.
> 코드 계약은 최신 `dev`를 ALPHA-879 브랜치에 병합한 상태를 기준으로 한다.

## 0. 결론

1. **2026-08-10 이후 포워드 데이터는 아직 검증할 수 없다.** 조사일은 8월 9일이다.
   모든 `>= 2026-08-10` 결과가 0인 것은 결손 증거가 아니라 미래 구간이기 때문이다.
   8월 10일 장 종료 후 §6의 동일 판정을 다시 실행해야 한다.
2. 에이전트가 Glue/Athena에서 발견할 수 있는 객체는 82개지만, 분석엔진의 코드 계약과
   직접 겹치는 Glue 정본은 `market_data_kr.edge_intraday_5m` 하나다. 나머지 CausalLake
   표면은 Postgres 클램프 뷰, S3 경로 직접 읽기, 분석 전용 백필 파일이다.
3. `edge_intraday_5m` Iceberg는 최대 2026-08-05이고 canonical 폴백은 2026-08-07이다.
   따라서 Glue 정본이 뒤처졌고, 현재 코드는 신선도 가드 실패 시 canonical로 폴백한다.
4. 수작업 canonical 7종은 모두 2026-07-31 또는 그 이전에서 멈췄고 collection log가 없다.
   경로는 canonical이지만 런타임 의미는 일회성 백필이다.
5. `analysis/backfill` 10종은 모두 파일 1개이며 2026-08-05에 함께 적재됐다. 파일 존재가
   포워드 생산을 뜻하지 않는다.
6. `edge_lake_draft`의 Iceberg 원표 21개 중 실제 Parquet 데이터 파일이 있는 것은
   `statement_line`과 `report_current`뿐이다. 나머지 19개 및 그 `_latest` 뷰는
   **발견 가능하지만 데이터로는 사용할 수 없다.**
7. 최신 `dev`에서 KIS 업종지수 분봉 TR은 더 이상 `[실측 필요]`가 아니다.
   `kis_sector_index.py`와 45종 코드 맵이 추가됐다. 다만 loader 주석대로 레인에는 아직
   배선되지 않아 **구현된 어댑터이지 포워드 데이터셋은 아니다.**

## 1. 판정 어휘와 근거

| 판정 | 의미 |
|---|---|
| 발견 가능 | Glue/코드 카탈로그에 이름과 스키마가 있다 |
| 접근 가능 | `work` IAM으로 Glue, Athena, S3 메타데이터를 읽을 수 있다 |
| 사용 가능 | 실제 데이터 파일/행이 있고 분석 계약의 스키마와 PIT 조건을 만족한다 |
| 포워드 | 2026-08-10 이후 정규 스케줄 실행과 산출물이 collection log로 증명된다 |
| 일회성 | 파일은 있으나 정규 writer와 collection log가 없다 |

근거 SSOT:

- 분석 표면: `src/apps/cloud/analysis-engine/src/edge_analysis/statics/duck.py`
- 에이전트 SQL 표면: `src/apps/cloud/analysis-engine/src/edge_analysis/adapters/sql_surface.py`
- 레이크 경로: `src/apps/cloud/data-pipeline/src/data_pipeline/lake/storage.py`
- ops 등록: `src/apps/cloud/data-pipeline/src/data_pipeline/ops/catalog.py`
- 업종지수 분봉: `src/apps/cloud/data-pipeline/src/data_pipeline/sources/kis_sector_index.py`
- 업종지수 설정 상태: `src/apps/cloud/data-pipeline/src/data_pipeline/config/loader.py`

제한:

- RDS `edge-dev`는 private endpoint다. 이번 세션에서는 네트워크 터널과 DB 자격증명을
  열지 않았으므로 Postgres의 실제 행수/최대일자는 **[실측 필요]**다.
- S3 날짜는 객체 키의 날짜 파티션, 포워드 실행은 collection log의 `started_date`로
  판정했다. Parquet 내부 날짜가 키와 다른지는 **[실측 필요]**다.
- `>= 2026-08-10`은 미래 구간이므로 §6 전까지 판정은 `PENDING`이다.

## 2. 에이전트가 발견·접근 가능한 표면

### 2.1 Glue/Athena 카탈로그: 82개

| DB | 객체 | 판정 |
|---|---|---|
| `default` | 없음 | 빈 DB |
| `edge_lake_draft` | Iceberg 원표 21 + 동일 이름의 `_latest` 뷰 21 | 42개 발견 가능, 실제 데이터는 2개 원표뿐 |
| `market_data_common` | `data_collection_log`, `news_articles`, `schema_migrations` | 3개 접근 가능, CausalLake 직접 계약 아님 |
| `market_data_kr` | 아래 21개 | `edge_intraday_5m`만 CausalLake 직접 계약 |
| `market_data_us` | 아래 16개 | 접근 가능하나 CausalLake 직접 계약 아님 |

`market_data_kr`:

`dg_market_daily_m`, `dg_market_daily_src`, `edge_intraday_5m`,
`edge_intraday_5m_local`, `edge_intraday_5m_src`, `ff5_factor_dataset`,
`fmp_kr_stock_industry_map`, `instrument_master`, `kr_ff5_factor_daily`,
`kr_ff5_formation_snapshot`, `kr_ff5_regression_result`,
`kr_ff5_security_factor_daily`, `kr_foreign_flow_daily`, `kr_market_cap_daily`,
`news_article_mentions`, `news_articles`, `price_daily`, `price_intraday`,
`raw_fundamental_statement`, `security_factor_daily`, `security_fundamental_daily`.

`market_data_us`:

`etf_holdings_snapshot`, `event_return_daily_result`, `ff5_factor_dataset`,
`instrument_master`, `news_article_mentions`, `news_articles`, `price_daily`,
`price_intraday`, `raw_fundamental_statement`, `security_factor_daily`,
`security_fundamental_daily`, `us_analysis_table`, `us_ff5_factor_daily`,
`us_ff5_factors`, `us_fmp_news_articles`, `us_news_articles`.

### 2.2 `edge_lake_draft`: 뷰가 있어도 데이터는 대부분 없다

| 상태 | 원표 | 실측 |
|---|---|---|
| 데이터 있음 | `statement_line` | Parquet 149개, 21,960,859 bytes, 최종 변경 2026-07-31 |
| 데이터 있음 | `report_current` | Parquet 1개, 212,535 bytes, 최종 변경 2026-07-31 |
| 메타데이터만 | `affiliation_edge`, `board_snapshot`, `company_event`, `company_master`, `consensus_point`, `credit_rating`, `entity_master`, `estimate_line`, `filing_meta`, `financial_metric`, `officer_tenure`, `person_link`, `person_master`, `report_basic`, `report_entity`, `report_estimative`, `report_section`, `report_warning`, `shareholder_stake` | 각 객체 1개, Parquet 0개, 최종 변경 2026-07-31 |

각 원표의 `_latest`는 별도 데이터가 아니라 원표를 읽는 Glue `VIRTUAL_VIEW`다. 따라서
메타데이터-only 원표의 `_latest`도 사용 가능 데이터로 세면 안 된다.

### 2.3 분석엔진 코드 계약

#### RDB 계약 20표

`price_daily`, `investor_flow_daily`, `etf_holding_snapshot`, `etf_nav_daily`,
`source_event`, `event_argument`, `instrument`, `instrument_classification`,
`supply_contract_fact`, `price_movement_trigger`, `etf_contribution_observation`,
`etf_contribution_member`, `event_thread`, `event_thread_link`, `entity`, `document`,
`news_document`, `event_evidence`, `document_assertion`, `event_measure`.

에이전트 자유 SQL에는 기반표 직접 접근을 금지하고 다음 PIT 표면을 제공한다:
`v_event`, `v_measure`, `v_instrument`, `v_daily`, `v_hold`, `v_flow`,
`v_flow_intraday`, `v_liquidity`, `v_nav`, `v_cohort`. 실제 RDB 커버리지는
private RDS 직접 조회 전까지 **[실측 필요]**다.

#### S3 계약 33셋

| 무리 | 뷰 |
|---|---|
| 상시 canonical/feature 후보 | `s3_price_daily`, `s3_investor_flow`, `s3_etf_nav`, `s3_news_articles`, `s3_etf_holdings`, `s3_etf_profile`, `s3_segment_fact`, `s3_supply_fact`, `s3_assertions` |
| 일회성 canonical | `s3_fx_daily`, `s3_index_daily`, `s3_rates_daily`, `s3_analyst_target`, `s3_rating_dist`, `s3_investor_value`, `s3_program_trading` |
| canonical 5분봉 | `s3_intraday_5m` |
| DataGuide curated | `s3_dg_items`, `s3_dg_financials`, `s3_dg_flow`, `s3_dg_price`, `s3_dg_consensus`, `s3_dg_market` |
| 수작업 raw 5분봉 | `s3_kr_5min`, `s3_us_5min` |
| draft Iceberg | `s3_statement_line`, `s3_estimate_line`, `s3_shareholder`, `s3_officer_tenure`, `s3_credit_rating`, `s3_person_master`, `s3_entity_master`, `s3_report_warning` |

별도 `bars_5m` 논리 뷰는 우선 `market_data_kr.edge_intraday_5m`을 쓰고 신선도 가드가
실패하면 `s3_intraday_5m`으로 폴백한다.

#### 분석 전용 백필 계약 10셋

`us_market`, `fx_usdkrw`, `tau_sidecar`, `layers_daily`, `etf_holdings_fmp`,
`pit_daily`, `fin_annual`, `flow_daily`, `sector_index`, `sector_member`.

## 3. 2026-08-09 런타임 신선도와 요구 데이터 괴리

### 3.1 상시 생산 증거가 있는 표면

| 표면 | 최대 데이터일 | collection log 최대일 | 판정 |
|---|---:|---:|---|
| KIS `price_daily` | 2026-08-07 | 2026-08-07 | 거래일 기준 정상 후보 |
| KIS `investor_flow_daily` | 2026-08-07 | 2026-08-07 | 거래일 기준 정상 후보 |
| KIS `investor_flow_intraday` | 2026-08-07 | 2026-08-07 | 상시 증거 있음 |
| KIS `etf_nav` | 2026-08-07 | 2026-08-07 | 거래일 기준 정상 후보 |
| KRX/KIS/FMP `etf_holdings` | 2026-08-07 | KRX 2026-08-07, FMP 2026-07-24 | KR 포워드, US/FMP 중단 |
| KIS `etf_profile` | 2026-08-07 | 2026-08-07 | 거래일 기준 정상 후보 |
| 뉴스/articles/assertions | 2026-08-09 | BigKinds 2026-08-09 | 주말 포함 포워드 증거 있음 |
| supply contract fact | 2026-08-07 | DART disclosures 2026-08-08 | 상시 증거 있음 |
| business segment fact | 2026-07-24 | DART financial statements 2026-08-07 | **상류는 돌았으나 산출 파티션이 뒤처짐** |
| canonical `intraday_5m` | 2026-08-07 | 별도 minute ledger | 폴백 데이터 있음 |
| Glue `edge_intraday_5m` | 2026-08-05 | 별도 minute ledger | **정본 Glue가 canonical보다 2거래일 뒤처짐** |

FMP collection log는 `price_daily`, `financial_statements`, `stock_news`,
`etf_holdings`가 모두 2026-07-24에서 멈췄다. ALPHA-558 토글/한도 상태와 일치하지만,
상시 생산자로 간주하면 안 된다.

### 3.2 수작업 canonical 7종

| 데이터셋 | 객체 수 | 최대 데이터일 | 최종 객체 변경 | collection log | 판정 |
|---|---:|---:|---:|---|---|
| `fx_daily` | 735 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `index_daily` | 582 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `rates_daily` | 292 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `investor_value_daily` | 670 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `program_trading_daily` | 285 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `analyst_target` | 228 | 2026-07-31 | 2026-08-02 | 없음 | 일회성 |
| `rating_distribution` | 14 | 2026-07-01 | 2026-08-02 | 없음 | 일회성, 월 grain |

### 3.3 `analysis/backfill` 10종

| 세트 | 크기(bytes) | 최종 변경 | 포워드 판정 |
|---|---:|---:|---|
| `us_market` | 6,760 | 2026-08-05 | 없음 |
| `fx_usdkrw` | 8,276 | 2026-08-05 | 없음 |
| `tau_sidecar` | 3,167,994 | 2026-08-05 | 수동 누적 코드만 있음 |
| `layers_daily` | 7,989,743 | 2026-08-05 | 생산 코드 없음 |
| `etf_holdings_fmp` | 71,169 | 2026-08-05 | 수동 |
| `pit_daily` | 105,951,623 | 2026-08-05 | 수동, DataGuide 종속 |
| `fin_annual` | 1,960,792 | 2026-08-05 | 수동, DataGuide 종속 |
| `flow_daily` | 24,597,043 | 2026-08-05 | 수동, DataGuide 종속 |
| `sector_index` | 205,775 | 2026-08-05 | 일봉 포워드 없음 |
| `sector_member` | 90,122 | 2026-08-05 | 분류 포워드 없음 |

## 4. 일회성 데이터의 정본 통합 계획

### 4.1 실행 순서

1. **관측 계약을 먼저 세운다.** storage builder, 단일 writer 소유권, collection log의
   `records_out`/`failed_records`, ops catalog 등록을 한 변경 단위로 만든다.
2. **같은 경로에 포워드 writer를 붙인다.** 수작업 canonical 7종은 데이터 이동 없이 현재
   경로를 유지한다. 2026-08-01 이후 공백은 같은 writer로 backfill run_id를 분리해 메운다.
3. **중복 분석 백필을 canonical 직독으로 교체한다.** 소비처 parity와 날짜 커버리지가
   통과한 뒤에만 `analysis/backfill` 파일 계약을 제거한다.
4. **draft는 자동 승격하지 않는다.** PIT, 라이선스, 정정 이력이 확보되지 않은 데이터는
   canonical로 옮기지 않는다.

### 4.2 데이터셋별 수렴

| 현재 표면 | 정본 목표 | 전환 조건 | 백필 계약 처리 |
|---|---|---|---|
| `fx_usdkrw` | `canonical/market_data/fx_daily` | FMP 한도 확인, builder+일배치+log, 2026-08-01~현재 backfill | `s3_fx_daily` parity 후 제거 |
| `us_market` | `canonical/market_data/index_daily` | 지수 심볼 계약, builder+일배치+log | `s3_index_daily` parity 후 제거 |
| `rates_daily` | 현재 canonical 경로 | FMP treasury step 상시화 | 백필 전용 별칭 없음 |
| `investor_value_daily` | 현재 canonical 경로 | KIS 13유형 long writer, 원 단위 변환, log | canonical 직독 유지 |
| `program_trading_daily` | 현재 canonical 경로 | KIS writer, 차익/비차익 NULL 계약, log | canonical 직독 유지 |
| `analyst_target` | 현재 canonical 경로 | 원천/라이선스 확정, published-at PIT, log | canonical 직독 유지 |
| `rating_distribution` | 현재 canonical 경로 | 월 스냅샷 writer와 유효월 계약, log | canonical 직독 유지 |
| `sector_index` | `canonical/market_data/sector_index_daily` | 일봉 source 확정, builder, 2026-08-04~공백 backfill, 기대 코드 집합 QC | 스키마 호환 뷰 전환 후 제거 |
| `sector_member` | `canonical/reference/sector_membership` | 날짜별 PIT 분류, alias/unmapped QC | 스키마 호환 뷰 전환 후 제거 |
| KIS 업종 분봉 | `canonical/market_data/sector_index_minute` | **어댑터 존재**, loader/worker/manifest/commit 배선과 dev 1세션 검증 필요 | 신규 정본, 일봉과 분리 |
| `layers_daily` | 유도 호환 뷰 | KR stock/ETF=`price_daily`, sector=`sector_index_daily`, US=`index_daily`, 이름=`etf_profile`/master | 12+ 소비처 parity 후 파일 제거 |
| `etf_holdings_fmp` | `canonical/holdings/etf_holdings` | KR=KRX, US=FMP로 파티션 writer 소유권 분리 | parity 후 제거 |
| `tau_sidecar` | canonical news + RDB `available_at` | 과거 뉴스 재파싱·재적재, 날짜별 누락 0, 09:00 폴백 제거 | 마지막으로 제거 |
| `pit_daily`, `fin_annual`, `flow_daily` | 미정 | DataGuide 라이선스·갱신 주체·주기 결정 | 결정 전 유지 |
| draft `statement_line` | draft 유지 | 정정 이력 소스와 PIT 재구축 가능성 확보 | canonical 승격 금지 |
| draft `report_current` | draft 유지 | 원천 lineage/PIT 및 포워드 writer 확보 | canonical 승격 금지 |
| metadata-only 19표 | 없음 또는 draft 재적재 | 실제 data file과 생산자 계약이 생길 때만 사용 가능 표시 | `_latest`를 데이터로 광고하지 않음 |

### 4.3 writer 소유권

- 기존 canonical 파티션과 신규 포워드 writer가 겹치면 안 된다. 컷오버 날짜를 코드 상수와
  운영 기록으로 고정하고, backfill writer는 컷오버 이전 파티션만 소유한다.
- `edge_intraday_5m`은 canonical 폴백보다 먼저 따라잡아야 한다. Iceberg 최대일이
  canonical 최대일보다 작으면 완료가 아니라 `INCOMPLETE`다.
- `layers_daily`는 새 물질화 writer를 만들지 않는다. 동일 원천을 복제하면 이름 오염과
  신선도 공백을 다시 만든다.

## 5. 최신 `dev`가 기존 스펙에 주는 변경

| 기존 가정 | 최신 코드/실측 | 문서 판정 |
|---|---|---|
| KIS 업종지수 분봉 TR 존재 여부 `[실측 필요]` | 전용 TR 어댑터, 파서, 45종 코드 맵과 테스트가 있음 | 존재 확인으로 갱신 |
| 업종지수 분봉을 1분 레인에 바로 재사용 가능 | loader가 명시적으로 “읽는 코드가 없다”고 기록 | 아직 포워드 아님 |
| RDB 19표 | `event_measure` 포함 `RDB_TABLES` 20표 | 20표로 갱신 |
| Glue 5분봉이 최신 정본 | Iceberg 8/5, canonical 8/7 | 정본 신선도 결손 |
| draft Iceberg 7셋에 데이터가 있음 | Glue 원표 다수는 metadata-only | 사용 가능 목록에서 제외 |

## 6. 2026-08-10 이후 재검증 게이트

8월 10일 KRX 장 종료와 각 배치 종료 후 다음을 모두 만족해야 포워드 `PASS`다.

| 검증 | PASS 조건 |
|---|---|
| collection log | 해당 source/dataset에 `started_date >= 2026-08-10`, `records_out > 0`, `failed_records = 0` |
| canonical 파티션 | 거래일 표면에 2026-08-10 파티션 존재 |
| Glue 5분봉 | `max(date(ts)) >= 2026-08-10`, 시장 프록시 `069500` 존재, 종목 100개 이상 |
| RDB 클램프 뷰 | 각 핵심 뷰의 최대 업무일과 행수 직접 조회 **[실측 필요]** |
| 수작업 7종 | 포워드 writer가 구현되기 전에는 8월 10일 파티션 부재가 예상 결과이며 `FAIL` 유지 |
| business segment | DART financial log와 canonical 최대 report date의 차이가 해소되거나 명시적 `VALID_EMPTY` |
| 업종지수 분봉 | adapter가 아니라 worker manifest/commit과 canonical artifact가 함께 존재 |

재검증 전 현재 상태는 다음과 같이 읽는다.

- `PASS`: 8월 9일까지 뉴스 포워드, 8월 7일까지 KR 거래일 상시 표면.
- `FAIL`: 수작업 canonical 7종의 상시화, Glue 5분봉 신선도, FMP 포워드.
- `PENDING`: 8월 10일 이후 실제 착지.
- `[실측 필요]`: private RDS 행 커버리지, Parquet 내부 날짜/행 품질.
