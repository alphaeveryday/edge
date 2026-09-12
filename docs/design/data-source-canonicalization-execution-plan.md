# 데이터 소스 정본화 실행·증명 계획 (ALPHA-879)

> 기준일: 2026-08-09 KST. 코드 계약은 `duck.py`의 `S3_SETS` 33개와
> `BACKFILL_SETS` 10개다. AWS 결과는 `work` 프로파일 dev 계정의 S3·Glue 및 실제
> `CausalLake` 바인딩 결과다. 2026-08-10 이후 포워드는 아직 발생하지 않았으므로
> **미검증**이며, 완료 조건을 통과하기 전에는 “정본화 완료”로 표시하지 않는다.

## 1. 결론

- 33개를 전부 canonical로 옮기는 것은 목표가 아니다. 수작업·비-PIT·라이선스 미확정
  입력을 canonical로 승격하면 “정본인데 갱신되지 않는 데이터”를 다시 만든다.
- 이미 canonical/feature인 상시 표면은 생산·완전성 계약을 강화한다.
- canonical에 한 번만 적재된 7개 표면은 경로를 유지하고 writer만 상시 생산자로 교체한다.
- DataGuide 6개와 draft Iceberg 8개는 소스·PIT·갱신 책임이 확정될 때까지 격리한다.
- raw FMP 2개와 analysis/backfill 10개는 canonical 대체 표면의 동등성 검증 후 격리·폐기한다.
- FMP 제거는 “대체 소스가 있을 것”이라는 계획으로 증명할 수 없다. FMP 자격증명 없이
  연속 2거래일 산출물이 전진한 실행 증거가 있어야 완료다.

## 2. S3 계약 33개 전수 처분표

실제 에이전트 조회는 33개 모두 바인딩되었고, 30개는 `LIMIT 1`까지 성공했다.
`s3_dg_financials`·`s3_dg_flow`·`s3_dg_price`는 1.3 GiB 제한에서 OOM이므로 등록은 되어도
에이전트가 안전하게 사용할 수 있는 표면이 아니다.

| # | 뷰 | 현 존/상태 | 2026-08-09 실측 | 목표 처분 | 완료 게이트 |
|---:|---|---|---|---|---|
| 1 | `s3_price_daily` | canonical 상시 | 08-07 | 유지 | 2거래일 연속 로그·파티션 전진 |
| 2 | `s3_investor_flow` | canonical 상시 | 08-07 | 유지 | 동일 |
| 3 | `s3_etf_nav` | canonical 상시 | 08-07 | 유지 | 동일 |
| 4 | `s3_news_articles` | canonical 상시 | 08-09 | 유지·τ 재적재 | 발행시각 PIT 검증 |
| 5 | `s3_etf_holdings` | canonical 상시 | 08-07 | KR=KRX 유지, US 소스 분리 | 시장별 writer 단일화 |
| 6 | `s3_etf_profile` | canonical 상시 | 08-07 | 유지·이름 정본 | 오염 이름 참조 0건 |
| 7 | `s3_segment_fact` | canonical 공시 | 07-24 | 유지 | `VALID_EMPTY`와 중단 구분 |
| 8 | `s3_supply_fact` | canonical 공시 | 08-07 | 유지 | 로그·파티션 전진 |
| 9 | `s3_assertions` | feature 상시 | 08-09 | feature 유지 | 입력·추론 로그 연결 |
| 10 | `s3_fx_daily` | canonical 일회성 | 07-31 | 비-FMP 상시 writer | 소스 결정 + 2거래일 무FMP 전진 |
| 11 | `s3_index_daily` | canonical 일회성 | 07-31 | KRX/KIS 계열 상시 writer | TR 실측·스키마 동등성 |
| 12 | `s3_rates_daily` | canonical 일회성 | 07-31 | BOK/공식 금리 소스 writer | 약관·시각·PIT 검증 |
| 13 | `s3_analyst_target` | canonical 일회성 | 07-31 | 승인된 목표주가 소스 | 소스·라이선스 결정 전 차단 |
| 14 | `s3_rating_dist` | canonical 일회성 | 07-01 | 승인된 컨센서스 소스 | 소스·라이선스 결정 전 차단 |
| 15 | `s3_investor_value` | canonical 일회성 | 07-31 | KIS 13유형 parity 시 상시화 | TR 실측·키/단위 동등성 |
| 16 | `s3_dg_items` | draft DataGuide | 조회 가능 | draft 유지 | 라이선스·owner·주기 결정 |
| 17 | `s3_program_trading` | canonical 일회성 | 07-31 | KIS parity 시 상시화 | TR 실측·NULL 의미 보존 |
| 18 | `s3_intraday_5m` | canonical/Glue 상시 | 08-07 | Glue 정본 유지 | ≥100종목+069500 신선도 |
| 19 | `s3_dg_financials` | draft gzip CSV | OOM | draft Parquet/Athena화 | 1.3 GiB에서 조회 성공 |
| 20 | `s3_dg_flow` | draft gzip CSV | OOM | draft Parquet/Athena화 | 동일 |
| 21 | `s3_dg_price` | draft gzip CSV | OOM | draft Parquet/Athena화 | 동일 |
| 22 | `s3_dg_consensus` | draft DataGuide | 조회 가능 | draft 유지 | 라이선스·owner·주기 결정 |
| 23 | `s3_dg_market` | draft DataGuide | 조회 가능 | draft 유지 | 동일 |
| 24 | `s3_kr_5min` | raw FMP 중단 | 절단 데이터 | canonical 전환 후 격리 | 소비 참조 0·기간 parity |
| 25 | `s3_us_5min` | raw FMP 중단 | 절단 데이터 | canonical 전환 후 격리 | 소비 참조 0·기간 parity |
| 26 | `s3_statement_line` | draft Iceberg, 비-PIT | 조회 가능 | draft 격리 | 정정 이력 소스 결정 전 승격 금지 |
| 27 | `s3_estimate_line` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 소비처·생산계획 결정 |
| 28 | `s3_shareholder` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |
| 29 | `s3_officer_tenure` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |
| 30 | `s3_credit_rating` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |
| 31 | `s3_person_master` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |
| 32 | `s3_entity_master` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |
| 33 | `s3_report_warning` | draft 메타데이터 | 0행 | `EMPTY_SCHEMA` 명시 | 동일 |

## 3. 일회성 canonical 7개를 FMP 없이 포워드하는 조건

| 데이터 | 제안 소스 | 현재 판단 |
|---|---|---|
| FX | BOK ECOS 또는 KIS 환율 | 레포에 상시 adapter 없음. 소스·시각 정의 **[실측 필요]** |
| 국내/해외 지수 | 국내 KRX, 해외 KIS 해외지수 | KIS TR·과거조회·재배포 조건 **[실측 필요]** |
| 금리 | BOK ECOS + 미국 재무부 등 공식 원천 | 새 adapter와 발표시각 PIT 정의 필요 |
| 목표주가 | 승인된 KIS/라이선스 데이터 | 무료 정본을 가정하지 않는다. 결정 전 포워드 불가 |
| 등급분포 | 승인된 컨센서스 데이터 | 무료 정본을 가정하지 않는다. 결정 전 포워드 불가 |
| 투자자 13유형 | 기존 KIS 수급 TR 확장 | 현 EOD 데이터와 완전 동등하지 않다. 필드 parity **[실측 필요]** |
| 프로그램매매 | KIS 프로그램매매 TR | 차익/비차익 제공 여부 **[실측 필요]** |

소스가 확정되면 `storage.py` 빌더, raw 좌표, canonical writer, collection log,
ops 카탈로그를 한 PR에서 함께 만든다. 관측되지 않는 collector는 배포하지 않는다.

## 4. analysis/backfill 10개 수렴 계획

| 세트 | 정본 대체 | FMP 없는 전환 |
|---|---|---|
| `sector_index` | `canonical/market_data/sector_index_daily` | KRX/pykrx writer |
| `sector_member` | `canonical/reference/sector_membership` | KRX 분류 스냅샷 writer |
| `layers_daily` | canonical 가격·지수·프로필에서 유도한 호환 뷰 | 외부 수집 없음 |
| `tau_sidecar` | 뉴스 canonical/RDB τ 재파싱·재적재 | 외부 수집 없음 |
| `us_market` | `s3_index_daily` | 비-FMP 지수 writer가 선행 |
| `fx_usdkrw` | `s3_fx_daily` | BOK/KIS writer가 선행 |
| `etf_holdings_fmp` | KR은 KRX, US는 발행사/승인 소스 | US 소스 결정 전 폐지 불가 |
| `pit_daily` | DataGuide snapshot 또는 새 PIT 표면 | 라이선스·갱신 owner 결정 전 유지 |
| `fin_annual` | DataGuide 또는 정정이력 포함 DART 표면 | 스키마/PIT parity 전 유지 |
| `flow_daily` | DataGuide 또는 KRX/KIS 장기 수급 표면 | 13유형 parity 전 유지 |

## 5. 이상 데이터 처리 규칙

“이상”이라는 이유만으로 삭제하지 않는다. 아래 상태를 구분하고 각 증거를 남긴다.

| 상태 | 대상 | 처리 |
|---|---|---|
| 값 오염 | `layers_daily` 이름 41/80 이력 | 이름 소비 제거, profile/master로 대체, parity 후 원본 격리 |
| 조회 불능 | DataGuide 3개 OOM | gzip 직독 제거, partitioned Parquet/Athena로 변환 |
| 불완전·절단 | raw FMP 5분봉 | retired 표시, canonical 기간 parity 후 격리·삭제 |
| 비-PIT | dartlab `statement_line` | draft 유지, canonical 승격 금지 |
| 빈 계약 | draft 7개 0행 | “사용 가능”이 아니라 `EMPTY_SCHEMA`로 광고 |
| 무응답 가능 | 모든 기대 비어있지 않은 표면 | 0행을 성공으로 보지 않고 `INCOMPLETE` 처리 |

삭제는 소비 코드 0건, writer IAM 철회, 대체 표면 parity, 격리 manifest·rollback 좌표가
모두 확보된 뒤 보존기간을 거쳐 수행한다.

## 6. 실행 순서

1. 33+10 계약 ID, 키, PIT 열, writer, 기대 주기를 기계 판독 inventory로 고정한다.
2. 신규 경로 빌더·단일 writer 경계·collection log·ops catalog를 collector보다 먼저 만든다.
3. KRX 업종지수·구성종목을 상시화하고 `layers_daily`를 canonical 유도 뷰로 교체한다.
4. KIS 실측으로 투자자 13유형·프로그램매매 parity를 확인한 뒤 2개 표면을 상시화한다.
5. FX·지수·금리를 승인된 비-FMP 공식 소스로 상시화한다.
6. 뉴스 τ를 재적재하고 `tau_sidecar`를 제거한다.
7. holdings를 KR KRX와 US 승인 소스로 분리한다.
8. 목표주가·등급분포 및 DataGuide는 라이선스·owner 결정 후에만 진행한다.
9. 이중쓰기 없이 shadow 비교하고, cutover 뒤 구 writer 권한을 철회한 다음 격리·삭제한다.

## 7. “완벽히 충족”을 주장할 수 있는 증명 게이트

| 요구 | 필수 증거 |
|---|---|
| 전수성 | AST 검사로 `S3_SETS=33`, `BACKFILL_SETS=10`, 미등록 경로 0건 |
| 에이전트 사용성 | 실제 `CausalLake` bind+대표 query. 목표는 성공 또는 명시적 `UNAVAILABLE`; OOM 0건 |
| 포워드 | 기대 실행 2회 연속 `records_out`, `failed_records=0`, `VALID/VALID_EMPTY`, 파티션 전진 |
| 무FMP | FMP secret/toggle 없이 실행, FMP egress 0, `source=fmp` 로그 0, 산출물 전진 |
| 내용 parity | 스키마·PK 유일성·PIT·키 coverage·집계 checksum·표본 diff 통과 |
| 멱등성 | 동일 입력 2회 실행 후 키·checksum 불변 |
| silent-zero 방지 | 기대 non-empty 표면의 0행은 반드시 `INCOMPLETE`와 경보 |
| 레거시 제거 | 코드 참조 0, writer 권한 0, old prefix 격리 manifest, rollback 좌표 |
| cutover | 구/신 호환 뷰의 정의된 기간 parity 후 소비자를 한 번에 전환 |

증거는 실행별 JSON manifest로 남기고 PR·배포 ID·S3 파티션·collection log를 연결한다.
한 항목이라도 실패하면 전체 완료가 아니라 해당 표면 `BLOCKED`다.

## 8. 2026-08-10 이후 검증

기준일이 2026-08-09이므로 “8월 10일부터 쌓인다”는 주장은 현재 검증할 수 없다.
2026-08-10 장 마감과 예약 실행 이후 다음을 수행하고, 가능하면 08-11까지 2거래일 연속 확인한다.

1. `started_date >= 2026-08-10` collection log와 `records_out/failed_records/data_status`를 대조한다.
2. 각 표면의 최대 business partition이 기대 날짜까지 전진했는지 검사한다.
3. 5분봉은 069500 존재와 고유 종목 100개 이상을 동시에 검사한다.
4. 기대 비어있지 않은 데이터가 0행이면 성공 처리하지 않고 원장·SNS 경보를 확인한다.
5. 7개 일회성 canonical과 10개 backfill의 max partition이 그대로면 “미포워드”로 확정한다.

따라서 현재 증명된 것은 계약 전수 조사와 7개 일회성 canonical·10개 backfill의 정지 상태다.
정본화와 무FMP 포워드 완료는 위 실행·증명 게이트를 통과한 뒤에만 선언한다.
