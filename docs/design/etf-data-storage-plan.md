# ETF 데이터 저장 경로 설계 초안

상태: 팀 검토용 제안 · 2026-09-28. 저장소 코드·문서 기준이며 S3/RDB 실측, 신규 수집기·테이블 구현은 하지 않았다.
예외: §10(분석 v2 원천 관측 — 매크로·재무·KIS 지수업종)은 2026-09-30 코드·로컬 검증까지 구현했고, 2026-10-02 dev 배포·첫 소량 수집을 마쳤다(정기 비활성 — §10 상태).

## 목적과 범위

수집기를 추가하기 전에 팀이 **어디에 쓰고, 무엇을 키로 연결하고, 어디서 읽는지** 합의한다.
대상은 ETF ORCA 설계에 필요한 지속 수집·정제·이력 보존·데이터 제공이다.
ETF 자체 수급과 구성종목 수급을 모두 확보하고 구성종목 가중합을 지원한다.
연속일수 계산, 종료 경계 판정, 최소 N일 표시, 카드 숨김 여부는 소비 측 책임이다.
수집·저장은 해당 판단을 위해 거래일별 실제 0·결측·잠정·확정 상태와 이력 확보 범위를 구분해 제공한다.
연속일수를 알아낼 때까지만 수집하거나 특정 화면의 조회 기간에 맞춰 이력을 잘라내지 않는다.
모델의 산식·예측 기간은 모델 담당 영역이며 데이터 수집의 선행 조건이 아니다. 가격 트리거는 기존 방식을 유지한다.

기존 경로의 코드 정본은 [storage.py](../../src/apps/cloud/data-pipeline/src/data_pipeline/lake/storage.py)다.
기존 수작업·백필 경로는 [데이터 소스 통일 스펙](data-source-unification-spec.md)을 따른다.
이 초안은 두 정본을 대체하지 않으며 신규 이름은 구현 전 제안이다.

## 1. 저장 계층과 읽는 계약

`<lake>`는 환경 설정의 기존 pipeline lake 버킷이다. 아래 경로는 모두 버킷 상대 키다.
새 버킷이나 모델별 원천 복사본을 만들지 않는다.

| 계층 | 저장 내용 | 쓰는 주체 | 읽는 주체·방법 |
|---|---|---|---|
| S3 raw | 공급자 응답 원본, 수집 실행별 이력 | 소스별 수집기 | 정제기가 raw manifest의 직접 키로 읽음 |
| S3 canonical | 단위·식별자를 맞춘 관측값. 날짜 파티션 Parquet은 현재 상태이고, `canonical/tables.py` Iceberg 표는 추가 전용 이력이다(`latest_view`·`as_of_sql`, ADR-0057 §1) | 데이터셋별 정제기 | 최신 탐색·조회용. 과거 분석 재현에 현재 파일만 사용하지 않음 |
| S3 operations_archive | 실행별 불변 정제 결과, manifest, 품질 기록 | 해당 단계 생산자 | 로더·재처리·과거 시점 조회의 입력 계보 |
| S3 feature | 기존 규약의 모델 추론 결과 | 추론 생산자 | 분석 소비자. 결정적 수급 가중합의 저장 위치는 별도 결정 |
| Cloud Event Store | 서비스가 읽는 관측값·가용성·필요한 피처 | 데이터 파이프라인 로더 | 기존 DB 계약을 따르는 서버·분석 소비자 |

분봉은 기존 artifact/manifest와 DB의 확정 세대 계약을 유지한다. 새 일배치 경로로 옮기지 않는다.
기존 5분봉 저장 형식(canonical `intraday_5m` 파티션)도 유지한다. Glue Iceberg 표는 canonical 전환이 끝날 때까지만 현행 1순위 소비·롤백용으로 둔다(목표는 canonical 단일 경로 — [현황 대장](lake-path-transition-ledger.md) §3.4). 이 초안의 일별 Parquet 규약을 모든 레인에 적용하지 않는다.
고객별 데이터·최종 노출 콘텐츠는 이 설계에 포함하지 않는다([저장 위치 기준](../domain/data-residency.md)).

## 2. 데이터셋별 경로와 행의 기준

표의 canonical 경로 아래 파일 형식은 Parquet이다. 기존 경로는 실제 구현을 유지한다.
신규 dataset 이름은 raw의 `dataset`과 실행별 archive의 `dataset`에도 동일하게 쓴다.
`market`은 기존 KR/US 어휘를 유지하고, 거시 계열은 종목 시장 대신 `series_id`로 식별한다.

| 데이터셋 | canonical 경로 | 논리 행 키·중요 필드 | 상태·조회용 DB |
|---|---|---|---|
| 가격 | `canonical/market_data/price_daily/market={market}/trade_date={date}/` | 시장·종목·거래일, OHLC·거래량·실제 거래대금·가격 조정 기준 | 기존. `price_daily` 재사용, 고저가 조회·거래대금 적재 보완 |
| ETF·구성종목 수급 | `canonical/market_data/investor_flow_daily/market={market}/trade_date={date}/` | 시장·종목·거래일, 투자자별 순매수 수량·금액 | 기존. `investor_flow_daily` 재사용. 종목 유형으로 대상 구분 |
| 편입비중 | `canonical/holdings/etf_holdings/market={market}/as_of_date={date}/` | 시장·ETF·편입자산·편입 기준일, 비중·자산 유형 | 기존. `etf_holding_snapshot` 및 상태 테이블 재사용 |
| ETF 상품 속성 | `canonical/reference/etf_profile/market={market}/as_of_date={date}/` | 시장·ETF·기준일, 추종 방식·환헤지 등 공급된 속성 | 기존 경로 확장. 기본 식별정보와 상품 속성의 DB 매핑 검토 |
| NAV | `canonical/market_data/etf_nav/market={market}/trade_date={date}/` | 시장·ETF·거래일, NAV | 기존. `etf_nav_daily` 재사용 |
| 환율·금리 | `canonical/market_data/fx_daily/`, `canonical/market_data/rates_daily/` | 계열·관측시각, 통화쌍 또는 국가·만기·단위 | 기존 백필 경로 재사용. 기존 파일 스키마·파티션 실측 후 상시 수집 연결 |
| 원자재 | `canonical/market_data/commodity_daily/series_id={series_id}/observation_date={date}/` | 계열·관측일, 현물/선물 구분·통화·단위 | 신규 제안. `commodity_daily` |
| 정책 일정 | `canonical/reference/policy_calendar/authority={authority}/as_of_date={date}/` | 기관·행사 ID·일정 버전, 예정시각·공개/수신시각 | 신규 제안. `policy_calendar` |
| 재무 관측 | `canonical/financials/financial_metric/market={market}/period_end={date}/` | 발행사·지표·회계기간·회계기준·공시/정정 버전, 통화·주식 단위 | **구현(§10)**. DB `financial_metric`. 기존 draft `statement_line`을 쓰지 않은 이유는 §10.1 |
| ETF 분배금 | `canonical/market_data/etf_distribution/market={market}/payment_date={date}/` | ETF·지급 건 식별자·지급일·정정 버전, 실제 지급 세전 1좌 금액 | 신규 제안. `etf_distribution` |
| ETF 발행좌수 | `canonical/market_data/etf_units_daily/market={market}/trade_date={date}/` | ETF·거래일, 좌수·분할/병합 기준 | 신규 제안. `etf_units_daily` |
| 가격 단위 변경 | `canonical/reference/corporate_action/market={market}/effective_date={date}/` | 종목·행사 ID·효력일·정정 버전, 분할/병합 비율 | 신규 제안. 기존 관련 원천·스키마 재사용 여부 조사 후 확정 |
| ETF별 거시 계열 매핑 | `canonical/reference/etf_series_mapping/market={market}/as_of_date={date}/` | ETF·용도·계열·유효기간, 지정 원자재·중앙은행 | 신규 제안. 모델이 아닌 데이터 설정 담당이 관리 |

뉴스·공시는 기존 `canonical/news/news_articles`·`canonical/disclosures/*`와 문서 저장 경로를 재사용한다.
환율·금리는 기존 경로가 있다는 뜻이며 현재까지 수집됐다는 뜻이 아니다. 기존 문서에는 일회성 백필로 기록되어 있다.
분석 v2의 매크로 5계열은 이 경로가 아니라 §10의 `macro_observation`에 둔다(시각 정의가 달라 재사용하지 않은 이유 §10.1).
총매수·총매도는 원천에서 제공할 때 별도 필드로 추가하며 순매수로 역산하지 않는다.

## 3. 실행별 저장 경로

신규 일배치의 원본 경로 제안:

```text
raw/source={vendor}/dataset={dataset}/market={market}/ingest_date={date}/run_id={run_id}/part-00000.ndjson
```

거시 계열의 신규 raw는 `market={market}` 대신 `series_id={series_id}`를 사용한다.
원본 포맷이 XML·ZIP 등인 기존 소스는 원본 형식을 유지한다. 기존 raw 경로를 일괄 변경하지 않는다.

기존 실행 기록 경로를 재사용한다:

```text
operations_archive/raw_run_manifests/dataset={dataset}/run_id={run_id}/manifest.json
operations_archive/canonical_run_manifests/dataset={dataset}/run_id={run_id}/manifest.json
operations_archive/canonical_run_artifacts/dataset={dataset}/run_id={run_id}/report_date={date}/part-00000.parquet
operations_archive/data_quality_logs/dataset={dataset}/checked_date={date}/run_id={run_id}/log.json
```

⚠️ `operations_archive/canonical_run_artifacts/`에는 현재 **30일 만료** lifecycle이 걸려 있다(`infra/terraform/modules/pipeline/storage.tf` — 공시 재시도용 스냅샷 전제). §4의 과거 시점 조회가 이 artifact를 버전의 근거로 삼으려면, 신규 데이터셋의 artifact를 만료 없는 프리픽스에 두거나 보존 정책을 먼저 바꿔야 한다. 이는 미결정이다.

archive의 `report_date`는 현행 helper 명칭이다. 신규 데이터셋은 위 표의 거래일·관측일·회계기간말 등
어떤 날짜를 넣는지 manifest의 파티션 정의에 명시한다. 공개시각으로 간주하지 않는다.
기존 helper가 있다고 신규 데이터셋 지원까지 완료된 것은 아니다. dataset 등록·manifest 검증·소비자 연결은 구현 작업이다.

**경로 날짜의 시간대.** raw 의 `ingest_date`, 그것을 물려받는 artifact `report_date`, `collection_logs` 의 `started_date`, `data_quality_logs` 의 `checked_date` 는 **실행 시작 시각의 UTC 날짜**다(`write_raw_run`·`ingest_raw*` 모두 UTC). 업무 날짜(거래일·`as_of_date`·DAG 슬롯·run_key)는 KST 다. 그래서 00:00~09:00 KST 에 시작한 실행은 경로 날짜가 업무 날짜보다 하루 앞선다(2026-10-02 00:00 KST 업종 실행 → `ingest_date=2026-10-01`, `as_of_date=2026-10-02`). 정제·적재·재처리·`--all`·운영 원장은 날짜 없는 manifest 키·run_id 프리픽스로 찾고 artifact 키는 manifest 에서 읽으므로 영향이 없다(2026-10-02 점검). 소비자도 날짜로 경로를 조립하지 말고 manifest 나 run_id 로 찾는다.

manifest는 직접 객체 키·해시·행 수·입력 실행·출력 파티션·처리한 논리 키 범위를 제공한다.
불변 객체를 먼저 쓰고 검증 후 완료 manifest를 공개한다. 완료되지 않은 후보는 정상 입력으로 읽지 않는다.
같은 run_id로 다른 바이트를 덮어쓰지 않으며 재수집·정정은 새 실행으로 남긴다.
소비 완료 표시는 기존 소비자별 마커 계약을 따른다. 실패한 적재는 미소비 manifest에서 재시도한다.

## 4. 시점·정정·조회 규칙

- 관측 시점, 공급자 공개 시점, 실제 수신 시점, 편입/행사 효력일을 구분한다. 날짜만 있는 자료에 임의 시각을 만들지 않는다.
- 최신 canonical과 RDB upsert는 현재 상태 조회용이다. 덮어쓴 이전 값은 실행별 불변 artifact에서 보존한다.
- 과거 시점 T 조회는 **T까지 실제 수신했고, 알려진 공개시각도 T 이하인 버전**을 선택한다. 당시 편입비중도 같은 조건으로 고른다.
- 원천이 과거 정정 이력을 주지 않으면 지금 받은 최종값을 과거에도 알았던 값으로 취급하지 않는다. 보장 가능한 이력 시작일을 알린다.
- 신규 데이터셋에는 불변 artifact를 가리키는 버전 색인 조회가 필요하다. 초기에는 dataset별 완료 manifest로 연결하며, 규모상 필요할 때 DB 색인을 추가한다. 전체 이력에서 매번 raw를 스캔하는 방식은 서비스 조회 계약으로 삼지 않는다.
- RDB가 필요한 소비자에는 기존 테이블과 신규 테이블의 키·필드·가용시각 매핑을 제공한다. 신규 테이블명은 초안이며 Flyway 변경 전 DB 소비 담당과 합의한다.

기존 통일 스펙의 날짜 파티션 기반 PIT 표시는 이번의 정정 전 값 재현 보장과 범위가 다르다.
이 설계에서는 날짜 파티션만으로 시점 재현 완료라고 판정하지 않는다.

## 5. 구성종목 수급 가중합의 저장 계약

우선 구현 대상은 가중합에 필요한 구성종목 수급·편입비중·시점 이력의 저장이다.
아래는 후속 계산 결과의 논리 계약이며 계산기 구현을 원천 수집의 선행 조건으로 두지 않는다.
물리 경로는 미정이다. 초안의 `feature/market_data/etf_weighted_investor_flow` 제안은 철회한다.
현행 storage.py와 데이터 소스 통일 스펙은 feature를 비결정적 모델 추론 산출물로 구분한다.
결정적 집계를 조회 뷰로 제공할지, 별도 파생 데이터셋으로 저장할지는 저장 계층 ADR에서 결정한다.

행 키는 ETF·거래일·투자자·계산 정의 버전이다. 각 실행은 불변이며 manifest에서 입력 편입비중·수급 artifact를 참조한다.
금액은 `Σ(weight_fraction × constituent_net_amount)`로 계산한다. ETF 자체 순매수와 구분한다.
우선 제안은 해당 거래일에 유효한 편입비중 사용이다. 기간 합계는 일별 결과를 합산한다.
당일 비중의 실제 효력·가용 시각과 지연 제공 시 선택 규칙은 원천 확인 후 확정한다.

출력은 `weighted_net_amount`, `covered_weight`, `expected_weight`, `missing_constituent_ids`,
`holdings_as_of_date`, `formula_version`을 포함한다. 비중은 0~1 단위로 통일한다.
가중 대상은 수급이 있는 주식으로 명시하고 현금·기타 자산은 별도 제외 내역을 제공한다.
필수 대상 수급이 누락되면 확정 가중합은 null로 두고 커버리지를 반환한다. 남은 비중을 100%로 재정규화하지 않는다.
이 정의는 수급 가중 관측값이며 ETF 유입자금이나 가격 기여율이 아니다.
화면용 차트·밸류 계산값을 영속화할지는 소비 요구에 따라 별도 결정한다. 모델 출력 저장은 이번 범위 밖이다.

## 6. 팀 공유·검토 완료 기준

| 담당 영역 | 검토할 내용 | 산출물 |
|---|---|---|
| 데이터 파이프라인 | 공급자·수집 주기·시작 가능 이력·키·파티션·정정·결측 | 위 저장 경로표와 dataset별 필드 계약 |
| 분석/모델 소비 | 필요한 계열·단위·대상·관측주기·읽는 방식 | canonical/feature 또는 DB 입력 계약. 예측 기간 정의와 분리 |
| DB/서버 소비 | 기존 테이블 재사용·신규 스키마·가용성 표시 | Cloud Event Store 매핑과 예제 조회 |

구현 전 팀이 확인할 항목:

1. 기존 FX·금리·재무 백필의 실제 객체·필드·최신일을 확인하고 재사용 매핑을 채운다.
2. 신규 공급자, 수집 주기, 거래일 달력, 이력 보존 기간·비용 정책을 정한다. 모델 기간으로 수집 이력을 잘라내지 않는다.
3. 각 dataset에 정상 행·결측 행·정정 전후 행 예시와 읽는 경로를 붙인다.
4. 수급 가중합의 비중 효력 기준·가중 대상·미확보 비중 처리에 합의한다.
5. 수집 재실행의 멱등성, 정정 이력 보존, 과거 시점 재현, 누락 상태 노출을 완료 기준으로 삼는다.

이 문서는 공유 가능한 초안이다. 메시지 발송·PR 생성·팀 합의 완료를 뜻하지 않는다.

## 7. 기존 경로 정리와 전환 계획

> **보류(2026-09-28).** 이 절과 §8의 경로 통합·기존 데이터 이관·소비자 전환은 이번 작업에서 하지 않는다.
> 현황과 후속 목록은 [현황 대장](lake-path-transition-ledger.md) §6·§7에 있다. 신규 수집은 §9를 따른다.

코드 기준 우선순위다. 객체 수·용량·기간·writer·reader 실측과 데이터셋별 전환 상태는
[레이크 경로 현황 대장](lake-path-transition-ledger.md)(2026-09-28 dev)에 있다. 결정은 [ADR-0057](../adr/0057-lake-dataset-canonical-consumption-and-retirement.md)(제안됨)이다.
신규 수집 경로 확정 전에 아래 기존 표면과 중복 여부부터 확인한다.

| 우선순위 | 현재 경로·소비 | 권고 | 데이터 처리 |
|---|---|---|---|
| 높음 | `analysis/backfill/{name}.parquet`, `duck.py`의 BACKFILL_SETS | 원천 데이터와 재생성 가능한 가공물을 구분하고 정규 생산·조회로 전환 | `fx_usdkrw`·`us_market`는 기존 FX·지수 canonical과 중복 대조. `sector_index`·`sector_member`는 정규 경로 구축. `layers_daily`는 가능한 축부터 호환 뷰로 대체. 단순 폴더 이동으로 끝내지 않음 |
| 높음 | `draft/curated/source=dataguide/...`, `duck.py`·`pit.py`·`fin.py`·`flowhist.py`·`dgwide.py` 직접 참조 | 운영 소비가 임시 존에 의존하는 경계를 해소. 공급자 원본·정규 관측·가공 캐시의 역할 및 생산 담당 확정 | 원본 보존 후 검증된 세트만 정제·승격. `pit_daily`·`fin_annual`·`flow_daily`는 대체 자료 확보 전 유지 |
| 높음 | `raw/kr_intraday/fmp_5min/`, `raw/fmp_5min_us/`, gap 경로, 로컬 5분봉 | 원본 보관은 표준 raw로 수렴, 운영 조회는 기존 5분봉 정본으로 수렴 | KR·KR gap·US·US gap 모두 2026-09-28 원본 수신일 기준으로 이관(현황 대장 §3.1 — 07-29 KR 복사는 복사일 파티션이라 중복으로 남김). 원본 복사와 canonical/Glue 정제·적재는 별도. 로컬에만 있는 과거 구간도 이관 대상 |
| 조건부 | `draft/canonical/*` Iceberg | 소스 품질·시점 이력 조건 확인 후 데이터셋별 승격 | Glue DB·테이블 location·메타데이터를 함께 다뤄야 함. S3 prefix 복사만으로 승격하지 않음 |
| 이동 불필요 | 기존 `canonical/market_data/{price_daily,investor_flow_daily,fx_daily,rates_daily,...}` | 경로 유지. 빠진 경로 빌더·상시 생산·신선도 계약 보강 | 누락 이력 보충·단위 정합성 검증만 수행 |
| 유지 | 분봉 불변 artifact, operations_archive, RDB 조회용 복제 | 서로 다른 생명주기·감사·서비스 목적의 정상 분리 | 폴더 수를 줄이려고 병합하지 않음 |

기존 재무 `draft/canonical/financials/statement_line`과 DataGuide 재무가 있으므로,
§2의 신규 `financial_metric`은 독립 정본으로 즉시 추가하지 않는다. 기존 스키마의 재사용·조회 뷰로
충족되는지 먼저 확인한 뒤 신규 관측 또는 파생 데이터가 필요한 경우만 이름과 저장 형식을 확정한다.

### ADR와 구현을 묶는 단위

새 ADR 제안 주제는 **레이크 데이터셋 정본·운영 소비 경로·승격 및 폐기 절차**다.
기존 승인 ADR을 새 결론으로 덮어쓰지 않는다. 새 결정으로 바뀌는 조항만 기존 ADR에 대체 링크를 남긴다.
ADR-0043의 Dataset Contract·신선도 축은 재사용하고, ADR-0048의 설명 S3 폴백 폐기를
모든 시장 데이터 폴백 금지로 확대 해석하지 않는다.
상세 경로·필드·매핑·이관 목록은 이 문서와 통일 스펙에서 관리하고 ADR에는 결정과 이유를 남긴다.

데이터셋 하나의 전환 완료는 다음을 모두 포함한다:

1. 기존 객체·카탈로그·writer·reader·로컬 전용 이력 목록을 고정한다. 목적 경로·소유자·행 키·중복 우선순위를 결정한다.
2. 새 생산/정제 경로와 멱등 이관 코드를 준비한다. 기존 데이터는 삭제하지 않고 복사 또는 재정제한다.
3. 같은 기간·종목으로 누락 키·중복·값·단위·시점 이력·행 수를 대조한다. 이관 중 신규 유입분의 처리 기준점도 기록한다.
4. 소비 뷰를 병행 검증한 뒤 전환한다. `duck.py`뿐 아니라 직접 S3를 읽는 생산/가공 코드도 함께 수정한다.
5. 경로·스키마·신선도·재현성 검증을 통과하면 구 writer를 종료한다. 되돌릴 때의 reader 설정과 유입분 재처리 방법을 남긴다.
6. 구 경로의 활성 참조가 없고 보존 정책을 충족한 뒤 별도 삭제한다. 조회 전환 완료와 원본 폐기는 같은 시점으로 묶지 않는다.

5분봉은 현재 `_bars()`가 Glue Iceberg 표를 1순위로 읽고(`_bars_iceberg`), 요청일 착지가 모자라거나 연결에 실패할 때만 canonical과 로컬 이력을 합집합으로 읽으며 심볼도 정규화한다. 전환 검증은 두 경로를 모두 대상으로 한다(현황 대장 §3.4).
단순히 S3 주소만 바꾸면 과거 기간이 빠지거나 동일 종목이 분리될 수 있으므로 이 동작을 전환 검증에 포함한다.
이 작업은 데이터셋별 Jira 작업으로 나누되, 각 작업의 완료 조건에 생산자·소비자·이관·문서 갱신을 함께 둔다.

## 8. 구현 인계용 설계와 작업 순서

> **보류(2026-09-28)** — §7과 같다.

이 절은 구현을 위한 제안 기준이며 팀 승인 완료를 뜻하지 않는다. 현행 코드와 운영 실측이 다르면
차이를 먼저 기록하고 더 최근의 실행 증거로 전환 목록을 보정한다.

### 전환 후 구조

- 공급자 원본은 기존 표준 raw, 정규 관측은 기존 canonical/Glue, 실행 증거는 operations_archive에 둔다.
- 분석 소비자는 기존 CausalLake의 논리 뷰 이름을 유지한다. 먼저 뷰의 입력을 교체해 분석 로직 변경과 저장 경로 변경을 분리한다.
- 경로 문자열의 생성은 기존 `lake/storage.py`, Iceberg 테이블 선언은 기존 `canonical/tables.py`가 담당한다.
  분석 모듈의 경로 복제를 줄이되 이를 위해 새 범용 레지스트리·서비스를 만들지 않는다. 패키지 의존과 배포 경계를 확인해 기존 설정/선언을 재사용한다.
- 환경별 버킷은 설정으로 주입한다. 신규 코드에 dev 버킷을 하드코딩하지 않는다.
- 최신 조회와 과거 시점 조회를 구별한다. 복사 시각을 원자료 공개·가용 시각으로 바꾸지 않는다.

| 순서 | 작업 단위 | 목적 표면 | 전환 조건 |
|---|---|---|---|
| 0 | 객체·생산자·소비자 조사 및 ADR 제안 | dataset별 현재→목적 경로 대장 | S3/Glue/로컬 현황, 단위·심볼·기간·행 키·writer 소유권 확인 |
| 1 | `fx_usdkrw`·`us_market` 중복 정리 | 기존 `fx_daily`·`index_daily` + 기존 이름의 호환 뷰 | 동일 계열인지 확인, 필요한 과거 구간 보충, 계속 갱신하는 생산자 확보, 신구 조회 대조 통과 |
| 2 | 임시 raw 및 로컬 5분봉 정리 | 표준 `price_5min` raw 및 현행 Glue/5분 canonical | 로컬 전용 이력과 gap 수집분 반영, 기존 심볼·봉 시각·중복 우선순위 보존 |
| 3 | 업종 일봉·분류의 정규 생산 | `canonical/market_data/sector_index_daily`, `canonical/reference/sector_membership` | 기존 백필과 호환되는 조회 뷰 및 포워드 생산 확보 |
| 4 | `layers_daily` 의존 축소 | 기존 가격·지수 데이터 기반 호환 뷰 | 동일 종목·계열·가격 정의 확보. 불가한 축은 잔여 의존으로 명시 |
| 별도 | DataGuide·draft Iceberg 승격 | 검증된 데이터셋별 정규 관측 표면 | 갱신 담당·입력 의미·시점 품질·카탈로그 이전 방식 확정 후 착수 |

`us_market`과 index_daily의 실제 구성 계열이 같다고 이름만으로 가정하지 않는다.
`layers_daily`의 섹터 ETF를 KRX 업종지수로 바꾸는 것은 저장 이관이 아니라 분석 의미 변경이므로 이 작업에서 하지 않는다.
DataGuide의 지표를 KIS/FMP의 유사한 이름 지표로 대체하는 것도 동일성 검증 없이 하지 않는다.
0→1이 첫 구현 단위이며, 1이 원천 부재로 막히면 사유를 남기고 독립적인 2를 진행할 수 있다.

### 이관 산출물과 완료 판정

이관 도구는 dry-run·대상 범위 제한·재실행·중단 후 재개를 지원한다. 과도한 새 프레임워크 대신 기존 이관 도구를 확장한다.
이관 목록에는 원본 객체 키/버전(가용한 경우)/해시, 목적 객체 키, 작업 상태를 남긴다.
바이트 복사는 체크섬을, 재정제는 논리 키별 값·단위·기간·가용시각과 중복 해소 결과를 대조한다.
부분 실패는 성공으로 기록하지 않는다. Glue/Iceberg는 메타데이터 참조가 실제 새 데이터를 읽는지도 검증한다.

코드·테스트 완료, 데이터 이관 완료, 운영 소비 전환 완료, 구 경로 폐기는 각각 별도 상태로 보고한다.
실제 이관은 대상 환경·버킷·계정·데이터 범위를 명시한 실행 계획으로 진행한다.
이번 작업에서는 구 데이터 삭제를 수행하지 않는다. 유지 이유·폐기 조건·잔여 소비자만 문서화한다.
신규 데이터셋 수집 확대, 모델·연속일수·화면 정책, 트리거 변경은 이 전환 작업에 섞지 않는다.

## 9. 신규 수집 데이터의 저장 계약

상태: 제안. 새 수집을 붙일 때 이 계약을 따른다. 기존 경로의 통합·이관은 하지 않는다(§7 보류).
결정의 근거는 [ADR-0057](../adr/0057-lake-dataset-canonical-consumption-and-retirement.md)(제안됨) §1~§4다.

| 항목 | 규칙 |
|---|---|
| 경로 재사용 | 같은 의미의 기존 데이터셋이 있으면 그 경로·파일 스키마·파티션을 그대로 쓴다(§2의 "기존" 행: `price_daily`·`investor_flow_daily`·`etf_holdings`·`etf_profile`·`etf_nav`·`fx_daily`·`index_daily`·`rates_daily`). 이름이 같아도 정의가 다르면 기존 값을 덮지 않고 필드나 데이터셋을 가른다(실측 예: `fx_usdkrw`와 `fx_daily`의 `change_pct`). 경로 문자열은 `lake/storage.py` 빌더로 만들고, 버킷은 환경 설정으로 주입한다 |
| 원본 | `raw/source=…/dataset=…/{market=…\|series_id=…}/ingest_date=…/run_id=…/`(§3). 공급자 응답을 형식 그대로 둔다. 실행 단위로 불변이고 삭제하지 않는다. `ingest_date`는 실제 수신일이며 복사일로 바꾸지 않는다 |
| 정제 | `canonical/…` 날짜 파티션 Parquet은 **현재 상태**, 데이터셋·파티션마다 writer 하나다. 덮어쓴 이전 값은 실행별 불변 artifact(`operations_archive/canonical_run_artifacts/…`)에 남긴다. ⚠️ 그 프리픽스는 30일 만료라서(§3), 시점 이력 보존용 위치·보존 정책은 구현 전에 정한다 |
| 행 키 | §2 표의 논리 행 키다. 종목은 `market` + 거래소 코드, 거시 계열은 `series_id`로 식별한다. 파일 안에서 (행 키)는 유일해야 한다 |
| 단위 | 금액은 원화 원 단위(원천이 백만원이면 정제에서 환산하고 raw는 원형 유지), 수량은 주·좌, 가격은 거래 통화 원값, 비중은 0~1이다. 변화율은 정의를 필드 설명에 적는다(전일 대비인지 일중인지). 단위가 다른 값을 한 열에 섞지 않는다 |
| 시각 | 관측(거래일·기준일·봉 구간 시작), 공급자 공개시각(원천이 줄 때만), 실제 수신시각(raw 실행), 분석 가시시각(`available_at`)을 구분한다. 날짜만 있는 자료에 시각을 지어내지 않는다. 근사 규칙을 쓰면(예: 장 마감 후 고정 시각) 그 규칙을 manifest에 적는다 |
| 정정 이력 | 재수집·정정은 새 `run_id`로 쌓고, 같은 run의 바이트를 덮지 않는다. canonical은 가장 늦게 **수신된** 버전을 현재값으로 둔다. 과거 시점 T 조회는 "T까지 수신했고 공개시각이 T 이하인 버전"을 고른다(§4). 원천이 정정 이력을 주지 않으면 이력 보장 시작일을 알린다 |
| 중복 | 같은 run 안에서 행 키가 겹치는데 값이 같으면 하나로 접고 그 수를 품질 로그에 남긴다. 값이 다르면 그 키를 조용히 고르지 않고 무효로 격리해 드러낸다. 한 파티션에 파일이 여럿이면 파일 간 행 키가 서로소임을 코드로 보장한다 |
| 실제 0 · 결측 | 실제 0은 **행이 있고 값이 0**이다. 결측(수집 대상인데 값을 못 받음)은 값 행을 만들지 않고, 기존 완전성 기록(collection_log `ops`·`ops_expected_task`의 누락)과 manifest의 누락 키 목록으로 남긴다. 휴장일은 결측이 아니다(거래일 달력으로 판정). 결측을 0으로 채우지 않는다 |
| 잠정 · 확정 | 잠정 값은 확정 경로에 쓰지 않는다. 축이 다르면 데이터셋을 가른다(기존 규율: `investor_flow_intraday` ↔ `investor_flow_daily`). 같은 데이터셋에서 정정만 되는 값은 확정 여부를 필드로 남긴다 |
| 조회·담당 | raw는 소스별 수집기가 쓰고, canonical은 데이터셋별 정제기(데이터 파이프라인)가 쓴다. RDB는 로더가 적재한다. 소비자(분석·서버)는 canonical 최신 조회나 RDB를 읽고, 과거 시점 조회는 실행별 artifact와 가시시각 조건으로 한다. 모델 산식·예측 기간·연속일수·표시 정책은 소비 측 책임이다 |
| ETF 수급·편입비중 보존 | ETF 자체 수급과 구성종목 수급은 기존 `investor_flow_daily`에 종목 유형으로 구분해 둔다. 투자자별 순매수 수량·금액을 보존하고, 총매수·총매도는 원천이 줄 때만 둔다(역산 금지). 편입비중은 `etf_holdings`에 `as_of_date`별 스냅샷을 전부 보존하고 덮지 않는다. 화면 조회 기간이나 연속일수 계산 범위에 맞춰 이력을 잘라내지 않는다. 구성종목 가중합은 §5의 계약이며, 저장 위치는 미정이고 feature 존이 아니다 |


## 10. 분석 v2 원천 관측의 저장·소비 계약 (ALPHA-1130)

상태: **배포·첫 소량 수집 완료(2026-10-02, ALPHA-1136) — 정기 비활성.** dev DB 에 매크로 2계열·업종·재무 표본이 실렸다(실행 기록은 `src/apps/cloud/airflow/README.md` "첫 소량 실행 결과"). 원장 연결 실행·정기 활성화는 ALPHA-1140. §9를 적용한 첫 데이터셋 셋이다. 입력 요구는
"v2 필요 데이터와 확보 현황"(2026-09-26 조사): KODEX 반도체(091160)와 **각 분석 시점의** 전체 구성종목, 매크로 5계열.
결정(2026-09-30 확인): ① 과거 가시시각은 공급자 공개일 증거가 있는 자료(DART 접수일)만 재구성하고 매크로 백필은 수신시각 기준,
② 재무 Q4 = FY − 9개월 누적(유도 표시), ③ 연결 우선·연결 재무제표가 없는 회사만 별도.

코드: 공급자 해석 `sources/macro_series.py`·`dart_fundamental.py`·`kis_sector_master.py`, 저장·계보 `steps/source_observations.py`,
경로 빌더 `lake/storage.py`, DB `V202609301200__add_source_observations.sql`, DAG `airflow/dags/edge_source_daily.py`.

### 10.1 기존 표면을 쓰지 않은 이유

| 기존 표면 | 확인한 사실 | 판정 |
|---|---|---|
| `canonical/market_data/fx_daily`·`rates_daily` | [실측 2026-09-30] 레포 밖 일회성 적재(07-31 정지). `available_at`이 **시간대 없는 규칙값**(fx 다음날 06:00, rates 05:00)이고 수신시각이 없다. 값은 double, rates는 만기별 wide 형식. 레포에 writer·빌더 없음 | 재사용하지 않는다. 같은 열 이름에 다른 시각 정의를 섞게 된다(§9 "이름이 같아도 정의가 다르면 가른다"). v1 소비자(`paneltest`)용 생산자는 여전히 ALPHA-1105 몫이다. USD/KRW 공급자도 다르다(fx_daily는 FMP, 이 계약은 ECOS 15:30 종가 — §10.8) |
| `draft/canonical/financials/statement_line`(Iceberg) | 임시 존(ADR-0057 §1). writer가 Athena MERGE이고 스케줄 없음·dartlab 파티션만 읽음. `available_at`=접수번호 앞 8자리(목록 `rcept_dt`와 다를 수 있다 — `dart_disclosure` 주석). 주식총수(BPS 분모) 없음 | 재사용하지 않는다. 계정 원문 전체 보존은 raw가 한다 |
| DB `instrument_classification` | FMP 업종 텍스트 2열, PK `(instrument_id, as_of_date)`라 판본·출처를 못 가른다. 실데이터 사실상 빈 표(`tool_peer` 주석) | 재사용하지 않는다. KIS 지수업종은 다른 분류 체계다 |
| `analysis/backfill/sector_member` | pykrx KRX 업종지수 1단계, 분기 스냅샷 | 대·중·소 3단계 KIS 체계와 다르다. 섞지 않는다 |

### 10.2 데이터셋 요약

| | `macro_observation` | `financial_metric` | `sector_classification` |
|---|---|---|---|
| 공급자·계열 | ECOS `731Y003/D/0000003` 원/달러 종가 15:30(원, 1달러당 — FMP USDKRW는 현재 구독에서 402, §10.8) · FRED `DGS10`(%) — 2026-10-01 FMP `treasury-rates` `year10` 에서 교체, 같은 계열(§10.8 추기) · ECOS `817Y002/D/010210000` 국고채 10년(연%) · KOSIS `101/DT_1J22042` `T03` 총지수 전년동월비(%) · EIA `petroleum/pri/spt` `RBRTE` Europe Brent Spot FOB($/bbl) | OpenDART `list.json`(정기공시, 접수일) · `fnlttSinglAcntAll.json`(전체 재무제표, CFS·OFS) · `stockTotqySttus.json`(주식총수) | KIS 공개 마스터 ZIP `kospi_code.mst`·`kosdaq_code.mst`(지수업종 대·중·소 4자리) · `idxcode.mst`(업종명). KIS Open API 아님(키·토큰 없음) |
| 대상 | 5계열. 브렌트는 현물(선물 대체 금지), CPI는 공급자 공표 전년동월비(지수 수준 자체 계산 금지) | 구성종목(ETF 스냅샷에서 **기간별** 파생, §10.5) × 12월 결산 정기보고서. EPS 기본·희석, BPS, 매출액, 영업이익 | KOSPI·KOSDAQ 전 종목(ETF·ETN 포함, `security_group`로 구분) |
| 한 행 | (계열, 관측일)의 한 수집 실행 판본 | (회사, 사업연도, 기간, 지표, 기간 종류, 연결/별도)의 한 수집 실행 판본 | (시장, 종목, 받은 날)의 한 수집 실행 판본 |
| 논리 키 | `series_id, observation_date` | `corp_code, fiscal_year, fiscal_period, metric, period_kind, fs_basis` | `market, instrument_code, as_of_date` |
| DB 판본 키 | 논리 키 + `raw_run_id` | 논리 키 + `raw_run_id` | 논리 키 + `raw_run_id` |
| 단위 | 계열별 고정(DB CHECK): `KRW_per_USD`·`percent`·`USD_per_barrel`. 공급자 10진 문자열 그대로(반올림 없음) | 금액 `KRW`(원, 환산 없음 — `currency`≠KRW 행 거부), 주당 `KRW_per_share`. BPS만 소수 6자리 반올림 | 코드 원문 4자리 |
| 결측·0 | 값 행이 없음 = 미수집·미공표. 공급자 "데이터 없음" 응답은 `empty`(정상 0건)로 raw manifest에 남고, 오류는 `error` | 빈 칸·`-`는 결측(0 아님). 주식총수 표의 `-`만 0(자기주식 없음). 연결 없는 회사의 013은 `empty` | `0000` = 분류 없음 → 코드 NULL, 원문 `raw_*_code`에 보존 |
| 관측 시점 | 일별=관측일, 월별=기준월 1일(`reference_period` YYYY-MM) | `period_end`(분기말·기말), `fiscal_period` Q1~Q4·FY | `as_of_date`=받은 KST 날짜(원천이 기준일을 주지 않는다) |
| 공개시각 | 없음(다섯 공급자 모두 API로 주지 않는다) | `rcept_date`=목록 접수일(날짜만). 시각은 만들지 않는다 | 없음 |
| 수신시각 | `received_at`=응답 본문을 다 받은 시각(요청 시각이 아니다 — 응답이 오기 전엔 아무것도 보이지 않는다. 코드 `MacroSource.fetch`·`DartClient._get`은 응답 뒤에 `_now()`) | 같음 | 같음 |
| 가시시각 `available_at` | `received_at`(basis `received`만 허용 — DB CHECK) | `provider_release_date`: min(수신, 접수일 다음날 00:00 KST). 목록에서 접수일을 못 찾으면 `received` | `received_at` |
| 정정 선택 | 관측일마다 `available_at ≤ T`인 판본 중 가장 늦게 보인 것 | 같음. DART는 최신 제출본만 주므로 정정 전 원값은 복원하지 않는다 | 가장 늦게 보인 스냅샷 |
| 과거 이력 | 수집 시작 이후만(백필 값은 수신 이후에만 보인다 — 결정 ①) | 백필도 접수일 기준으로 과거에 보인다(결정 ①). 정정 전 값은 없음 | **현재값만.** 받기 전 날짜의 분류는 없다(복원 주장 없음) |

### 10.3 경로·파일·계보

| | raw (원본 형식) | canonical 현재 상태 (Parquet) | 실행별 artifact |
|---|---|---|---|
| 매크로 | `raw/source={ecos,fred,kosis,eia}/dataset=macro_observation/series_id={id}/ingest_date=/run_id=/{id}-{from}-{to}-{sha16}.json` | `canonical/market_data/macro_observation/series_id=/observation_date=/part-00000.parquet` | `operations_archive/canonical_run_artifacts/dataset=macro_observation/run_id=/report_date={ingest_date}/part-00000.parquet` |
| 재무 | `raw/source=dart/dataset=financial_metric/market=KR/ingest_date=/run_id=/{corp}-{종류}-{sha16}.json` (종류: 목록 `list-pN` · 재무제표 `{연도}-{보고서}-{CFS·OFS}` · 주식총수 `…-shares`) | `canonical/financials/financial_metric/market=KR/period_end=/part-00000.parquet` | 같은 규칙(`dataset=financial_metric`) |
| 업종 | `raw/source=kis/dataset=sector_classification/market={KOSPI,KOSDAQ,KR}/ingest_date=/run_id=/{파일}-{sha16}.zip` | `canonical/reference/sector_classification/market=/as_of_date=/part-00000.parquet` | 같은 규칙 |

`ingest_date`·`report_date` 는 UTC 실행일이다. canonical 파티션 날짜는 다른 축이다 — 매크로 `observation_date` 는 공급자 관측일, 재무 `period_end` 는 보고기간 말이라 실행일과 비교할 값이 아니다. 같은 실행일을 뜻하는 것은 업종 `as_of_date`(수신 KST 날짜)뿐이고, 이것만 00~09시 KST 실행에서 `ingest_date` 보다 하루 뒤다(§3 "경로 날짜의 시간대").

- raw 객체 이름에 내용 해시가 있어 불변이다(같은 바이트 재기록 no-op, 다른 바이트는 다른 키). **raw run manifest**
  (`operations_archive/raw_run_manifests/dataset=/run_id=/manifest.json`)에 오른 객체만 입력이다 — 키·sha256·바이트 수·
  인증키 없는 요청 서술·상태(`ok`·`empty`·`error`)·요청 창·구성종목 커버리지. 완료 manifest가 있으면 같은 run_id 재수집은
  공급자를 부르지 않는다.
- 정제는 raw manifest의 직접 키만 읽고 sha256을 대조한다. canonical 병합은 논리 키마다 `(received_at, raw_run_id)`가 큰
  판본을 남긴다 — **늦게 끝난 옛 실행이 최신값을 덮지 못한다**(적재 순서가 아니라 수신 순서). CAS(`put_bytes_if_version`)로
  파티션 경합을 막는다. **canonical run manifest**에 입력 raw manifest sha256·artifact 키/sha256·쓴 파티션 키/sha256·거부 수가 있다.
- 적재는 canonical manifest의 artifact를 sha256 대조 후 `INSERT … ON CONFLICT DO NOTHING`. 성공 커밋 뒤에만 소비 마커
  (`…/run_id=/consumed/load_source_observations.json`)를 쓴다. 저장 뒤 적재 전에 멈춘 실행은 `--all`이 이어 싣는다.
- 같은 실행 안의 중복 논리 키: 값이 같으면 접고(`collapsed_duplicates`), 다르면 그 키를 격리한다(`conflicting_duplicate`).
- DB 행마다 `raw_run_id·raw_key·raw_sha256·canonical_run_id·artifact_key·artifact_sha256`이 있어 조회 결과에서 원문까지 간다.
  재무는 `inputs`(JSONB)에 근거 줄(접수번호·보고서·계정 ID·금액 필드·값·raw 키)과 `formula`를 둔다.

**보존과 복구(2026-09-30 로컬 실측으로 확정 — `tests/e2e/test_source_observations_pg.py::test_artifact_expiry_recovery_paths`).**
과거 재현의 근거로 약속하는 것은 **만료가 없는 raw·raw manifest·canonical manifest 와 DB 판본 이력**이다. 실행별 artifact 는
`operations_archive/canonical_run_artifacts/` 아래 **30일 만료**(terraform `modules/pipeline/storage.tf`)라 재처리 캐시일 뿐이다.
artifact 가 만료된 뒤 경로별로:

| 경로 | 결과 | 근거 |
|---|---|---|
| 만료된 artifact 를 그 정제 run 으로 다시 적재(`load-* --input-run-id <정제 run>`) | **실패(exit 1, `load_error`), DB 불변** | artifact 바이트가 없다 |
| 같은 run_id 로 재정제(DAG `reprocess_slot` 이 하는 것) | **no-op — artifact 를 다시 만들지 않는다** → 이어지는 적재도 exit 1 | `normalize` 는 완료된 정제 run 을 덮지 않는다(덮으면 유효 결과가 `--all` 에서 빠진다). 즉 **DAG 재처리로는 만료 뒤 복구가 안 된다** |
| 같은 raw·**같은 코드 판**으로 **새 정제 run_id**(`normalize-* --run-id <새 id> --input-run-id <raw run>` → `load-* --input-run-id <새 id>`) | **지원** — artifact 바이트가 같아 이미 실린 행은 그대로, 안 실린 행(적재 전에 만료된 경우)은 실린다 | 정규화가 결정적(같은 입력 → 같은 Parquet 바이트, sha 대조) |
| 같은 raw·**다른 코드 판**으로 새 정제 run | 그 raw 의 행이 이미 DB 에 있으면 **적재 거부(exit 1)**, 없으면 새 규칙의 결과로 실린다 | 재정제 정체성 = artifact sha256(값·근거·가시시각 포함). 규칙을 바꿔 다시 싣는 것은 **새 수집 실행**으로만 |
| DB 에 이미 실린 결과 조회(`*_as_of`) | **영향 없음** | 조회는 DB 판본만 읽는다 |

**남는 한계(후속, 첫 수집의 필수 조건 아님).** ① 적재 전에 만료된 옛 정제 run 은 소비 마커가 없어 `load-* --all` 을 계속
실패(exit 1)시킨다 — 정기 DAG 는 `--input-run-id` 만 써서 영향이 없고, `--all` 복구를 쓸 때만 드러난다. 옛 run 을 "대체됨"으로
표시하는 도구는 없다(후속). ② 같은 run_id 재처리로 만료 뒤를 복구하는 경로는 없다(위 표 둘째 줄) — 새 정제 run_id 절차를
운영 절차로 쓴다(airflow README 인계 절). ③ 정확 재현은 같은 raw + 같은 `code_version` + 같은 스키마일 때만 주장한다.
`code_version` 은 이미지에 굽는 `GIT_SHA`(#1014 머지 뒤) — 그 전에 만든 manifest 는 `unknown` 이다. 카탈로그 버전
`OPS_CATALOG_VERSION` 은 코드 판이 아니라 쓰지 않는다. artifact 장기 보존(만료 없는 프리픽스)은 제안만 하고 적용하지 않았다.

### 10.4 DB와 소비 조회

테이블: `macro_observation`·`financial_metric`·`sector_classification`(추가 전용 판본). 소비 계약은 아래 함수다
(함수가 판본 선택 규칙의 정본이고, 소비자는 테이블을 직접 거르지 않는다).

| 함수 | 반환 | 규칙 |
|---|---|---|
| `macro_observations_as_of(T, series, n=21)` | 최근 n개 관측(관측일·`reference_period`·값·단위·수신·가시·근거 키) | 관측일마다 `available_at ≤ T` 판본 중 최신. 문서의 "최근 공개 2관측일"은 n=2, 툴 탐색 한도 21과 섞지 않는다 |
| `financial_quarters_as_of(T, instrument_code)` | 분기별 EPS(해당 분기)·BPS(분기말, 보통주 기준 `bps`·통상 `bps_total_shares`·`bps_note`)·매출·영업이익과 각 유도 표시·접수번호·run | 누적값은 돌려주지 않는다. 기준(연결/별도)은 회사 단위로 고정 — T까지 연결이 한 번이라도 보이면 연결. **행 단위는 확정 보고서 판본이다**(`financial_report_version` — 회사·연도·보고기간·기준마다 `available_at ≤ T` 인 CONFIRMED 판본 중 가장 늦게 **받은** 것). 그 실행이 만든 지표만 값이 있고 빠진 지표는 NULL 이지 옛 실행의 값이 아니다 — 지표가 0개인 정정 판본도 한 줄이라 옛 값이 최신처럼 남지 않는다(리뷰 5~9차 잔여 ⑥ 해소). UNCONFIRMED 판본(HTTP 오류·파손·다른 보고서 응답)은 선택에 끼지 않아 일시 실패가 확정값을 무효화하지 않고, 확정 판본보다 늦은 실패는 `latest_unconfirmed_at` 으로만 드러난다. 늦게 끝난 옛 실행은 수신시각이 앞서 최신 확정을 덮지 못한다. 같은 접수번호의 재수집은 모두 원 공개일부터 보이고(결정 ①) 그중 가장 늦게 받은 것이 이긴다; 새 접수번호(정정)는 그 접수일부터만 보인다. 확정이 한 번도 없는 보고서는 가장 늦은 UNCONFIRMED 시도가 값 NULL·`version_status=UNCONFIRMED` 행으로 나온다(어댑터 gap `REPORT_UNCONFIRMED`) — 소비자가 실패한 확인 시도를 본다. 진행 중·중단된 실행은 판본이 없다(아무것도 바꾸지 않는다). 연결/별도 기준은 **지표가 있는 확정 연결 판본 이력**으로 정한다 — 최신 연결 판본이 비어도 별도 값으로 갈아타지 않는다. 파손 행이 섞인 응답(malformed)·정제가 거부한 분모 응답은 확인된 응답이 아니다(판본 UNCONFIRMED / `shares=error`). **값을 읽는 줄의 칸을 읽지 못한 재무제표 응답도 판본 UNCONFIRMED 다**(ALPHA-1172, `statement_detail=unreadable_line:<사유>`) — 읽는 계정 줄의 재무제표 종류 칸이 문자열이 아니거나 빔(`statement_kind_unreadable`), 후보 줄의 계정명이 문자열이 아님(`account_name_unreadable`), 통화 칸이 없거나 세 글자 코드가 아님(`currency_unreadable`), 금액 칸이 비지 않았는데 숫자로 읽히지 않음(`amount_unreadable`). 그 실행은 그 보고서의 지표를 싣지 않고, 조회는 옛 확정 판본을 그대로 준다(정정 내용을 반영한 것이 아니라 옛 확정값을 유지한 것이다). 빈 칸(원천 부재)·지원하지 않는 계정·세 글자 코드로 적힌 외화(정책 차단)·숫자로 온 금액과 사업연도·읽지 않는 줄의 파손은 판본을 내리지 않는다(읽지 않는 줄의 파손과 계정 id 가 깨진 줄은 실패 분류로만 드러난다). Q4 행은 사업보고서 판본의 것이다. 주식수 표는 한 표여야 한다 — 같은 종류 행이 둘 이상이고 서로 다르거나, 종류별 행의 접수번호·기준일이 다르거나, 기준일이 보고기간 말과 다르거나, 수가 정수가 아니거나, 종류별 수가 합계와 모순이면(두 종류가 다 있는데 합이 합계와 다름, 또는 한 종류만 있는데 그 수가 합계를 넘음 — ALPHA-1172) 거부. 거부된 표로는 `bps`·`bps_total_shares` 를 모두 만들지 않는다(`shares=error`). `version_rejected` 에 그 판본이 못 만든 지표와 사유가 있다. `bps_note`: 정제가 `bps_total_shares` 근거 줄에 남긴 판정 `common_bps`(`computed`·`bps_blocked_preferred_shares`·`bps_share_rows_unreadable`·`bps_input_missing` — 파손이 정책보다 먼저)를 조회가 그대로 읽는다: `PREFERRED_SHARES_PRESENT`(정책 차단, §10.9 ①) / `COMMON_SHARE_BPS_UNAVAILABLE`(주식수 파손) / `BPS_ABSENT_IN_LATEST_VERSION`(분모 응답은 정상(ok·013)인데 BPS 없음 — 확정된 부재) / `BPS_UNCONFIRMED`(분모 응답 실패 — 확정 못 함, 재수집 대상) |
| `sector_classification_as_of(T, codes[])` | 종목별 최신 스냅샷의 대·중·소 코드·이름 | `found=false`(그 시점 스냅샷에 없음) ≠ 코드 NULL(원천 `0000`) |
| `etf_constituent_source_coverage(etf, T)` | T에 유효한 구성종목 스냅샷(기존 `etf_holding_snapshot`+status good 판정)의 종목별 업종·재무 확보 여부 | 스냅샷이 없으면 0행 — 현재 구성으로 대신하지 않는다. **한계**: holdings 표는 ETF·날짜당 한 판본(기존 적재가 덮어쓴다)이라 정정 스냅샷 뒤엔 그 날짜의 이전 구성을 복원하지 못한다(정정 전 T 는 그 날짜를 건너뛴다) — 구성종목 판본 이력은 holdings 레인 소관. 0행의 사유를 구분하지 못하고 v2 런타임과 스냅샷 선택 규칙이 달라 **v2 실행 가능 보장이 아니다**(§10.11) |

예제(로컬 PostgreSQL 검증, `tests/e2e/test_source_observations_pg.py`):

```sql
-- 사업보고서 접수일 2026-03-10: 당일 23:59 에는 2025-Q4 가 없고, 다음날 00:00 부터 Q4(FY−9M) 가 보인다
SELECT period, eps, eps_derivation FROM financial_quarters_as_of('2026-03-10 23:59+09', '005930');  -- 2025-Q3 만
SELECT period, eps, eps_derivation FROM financial_quarters_as_of('2026-03-11 00:00+09', '005930');  -- + 2025-Q4 1100 FY_MINUS_9M
-- 정정: 새 값은 그 판본의 수신 이후에만, 그 전 기준시각은 옛 값
SELECT observation_date, value FROM macro_observations_as_of(:t, 'usd_krw', 2);
```

| `financial_report_version` (표) | 회사·연도·보고서(11013/11012/11014/11011)·연결/별도 × 수집 실행 = 한 줄: `status`(CONFIRMED·UNCONFIRMED), `metrics`(만든 지표), `rejected`(못 만든 지표·사유), `detail`(statement·shares 응답 상태), 접수번호·가시시각·근거 | 정제(`normalize-financial-metric`)가 지표 artifact 와 함께 둘째 artifact(`companion`)로 만들고, 적재가 **같은 트랜잭션**으로 싣는다(소비 마커는 커밋 뒤) — 한쪽만 실린 상태가 없다. 현재 상태 파티션은 없다(실행마다 새 사실). 조회 함수만 읽는다(테이블 권한 0) |
| `source_observation_freshness()` | 데이터셋(매크로는 계열)별 최신 관측일·마지막 수신·마지막 적재 성공(원장 `LOAD_*` FULFILLED)·상태 — 재무는 판본 표로 센다(지표 0개 판본도 적재 사실) | 상태는 항상 `UNKNOWN`/`NO_PROVIDER_CALENDAR`(§10.6). 행 없음 = 적재 0건 |

**권한**: v2 쓰기 역할(`edge_analysis_v2_writer`)의 테이블 권한은 그대로 0이다. 읽기는 위 다섯 함수의 EXECUTE만 — 함수는
`SECURITY DEFINER`(`search_path=public` 고정)로 소유자 권한에서 테이블을 읽고, PUBLIC의 EXECUTE는 회수했다.
`tests/analysis_v2_writer.sql`이 셋을 따로 검사한다: ① 다섯 함수 실행 가능, ② PUBLIC에 열린 DEFINER 함수 없음·다른 DEFINER
함수 실행 불가, ③ 네 테이블(판본 표 포함) SELECT·INSERT 거부(변이 4종으로 확인: EXECUTE 회수·PUBLIC 부여·테이블 SELECT 부여·INVOKER 전환 모두 실패). `search_path = public, pg_temp`.

**v2 어댑터**: `edge_analysis_v2/storage/source_inputs.py` — `macro_inputs(conn, T)`·`financial_inputs(conn, T, codes)`·`freshness(conn)`가
함수 결과를 fixture 행 형태로 만든다. 매크로 `observed_at`은 **관측일 문자열 그대로**(경계 시각으로 바꾸지 않는다 — 소비 코드
`tools/fixture_data/common.observed()`가 그 한국 날짜가 끝난 뒤부터 관측된 것으로 센다). 결측은 행이 아니라 `gaps`로 돌아온다
(`no_observation_visible`·`no_release_visible`·`EPS_ABSENT_IN_LATEST_VERSION`·`bps_note` 세 값) — 0으로 채우지 않는다. 결손 분기는 행에서 빼지 않고 `None` 으로 남긴다 — 빼면 `valuation.calculate` 가 그 앞 4분기로 미끄러져 옛 비율을 현재값처럼 낸다.
재무 행마다 `version`(권위 판본 run·수신시각·`latest_unconfirmed_at`·분모 응답 상태)이 붙어 소비자가 "옛 판본을 읽고 있고 그 뒤 확인이 실패했다"를 안다. 통합 테스트 `integration_tests/test_source_inputs_postgres.py`(로컬 PG `127.0.0.1:55445/edge` 로 고정, `V2_SOURCE_TEST_DSN`)가 writer 역할로 함수를 읽어
`macro.compare`·`valuation.calculate`까지 돌린다(접수일 다음날 00:00 경계, 우선주 회사의 BPS gap, 지표 0개 판본·미확정 확인·분모 실패, 테이블 직접 읽기 거부 포함). 실 파이프라인 경로는 `data-pipeline/tests/e2e/test_source_observations_pg.py`(수집→정제→적재→조회: 지표 0개 정정·일부 지표·공급자 실패 vs 013·늦은 옛 실행·중단 후 `--all` 복구·중복 정제).

**소비 정책(2026-09-30 적용)**: ① 우선주 회사의 보통주 BPS 차단 유지 — `bps_total_shares` 는 근거와 함께 보존만, 자동 대체 없음. `valuation.weighted` 는 구성종목 하나라도 BPS 가 없으면 전체 가중 PBR 을 내지 않는다(부분 커버리지 ETF PBR 없음 — 기존 계약 "결측 종목을 빼거나 재정규화하지 않음" 그대로; 커버리지 하한은 계약에 없어 만들지 않았다) 하고, 결과에 `coverage{constituents, weight}` 를 싣는다. ② Q4 EPS: `FY_MINUS_9M` 값·근거는 보존, `valuation.calculate/weighted` 결과가 `approximate`·`derived_periods`·`derived_constituents` 로 근사 여부를 싣는다(툴 결과는 에이전트에 그대로 전달·감사 저장). 요인 화면 `get_instrument_factors`(dev #1002 로 카드 툴을 대체)도 `eps_approximate`·`eps_derived_periods`·`weighted_per_approximate` 를 싣는다 — 근사 표시 없이 PER 을 내는 소비 경로는 없다(옛 카드 함수 `valuation.metrics` 는 유도 분기가 있으면 PER 카드를 뺀다). TTM 은 연속 4분기 인덱스로 고정 — 같은 분기 재공개는 한 기간, 결손 분기(EPS·BPS None)는 오류(앞 4분기로 미끄러지지 않음). ③ USD/KRW = ECOS `731Y003/0000003`(종가 15:30) 고정 — 02:00 계열(`0000013`)로 전환·혼합하지 않는다(DB CHECK 가 계열·공급자 쌍을 강제).

### 10.5 수집 주기·백필·재시도·writer

- **writer**: 데이터셋·파티션마다 정제 스텝 하나(`normalize-{macro,sector,financial-metric}`). raw는 수집 스텝, DB는 적재 스텝.
- **정기**: DAG `edge_source_daily` 매일 05:20 KST 한 슬롯(06:00 전망 배치 전에 적재가 끝나는 시각 — 근거는 DAG 도크스트링). 매크로 창 = 어제 − (소급일 − 1) ~ 어제, 월별(CPI)은 시작을 그 달 1일로 맞춘다
  (USD/KRW·금리 14일, CPI 124일, 브렌트 28일 — 늦은 게시·정정 흡수). 재무 창 = 접수일 오늘−14 ~ 오늘. 업종 = 거래일만.
- **백필**: 같은 DAG를 수동 trigger + `macro_from/to`·`financial_from/to`(CLI `--from/--to`). 매크로 `to`≤어제, 재무 `to`≤오늘 —
  미래·진행 중 관측은 스텝이 거부한다. 업종은 현재값만이라 백필 인자가 없다(`ingest-raw-sector`가 `--from/--to` 거부).
  한 run은 1500초 안이어야 한다 — 긴 기간은 1년 단위로 나눈다. **실제 확보 기간은 확정하지 않았다**: 평가 날짜가 정해지면
  그 기간을 인자로 준다(최소 이력: EPS 4분기·BPS 1분기·매출/영업이익 2분기 + 재생 준비기간).
- **결산월**: 회사 단위로 사업보고서가 정한다 — 목록(소급 400일)에 12월 사업보고서만 있어야 12월 결산으로 보고 그 회사의 정기보고서를 계획한다. 비12월 사업보고서가 있으면 `non_december_fiscal_year`, 사업보고서가 목록에 없으면(창 안 분기보고서의 다음 해 목록을 한 번 더 받은 뒤에도) `fiscal_calendar_unconfirmed` 로 그 회사 전체를 거부한다 — 6월 결산 회사의 9월 분기(=1분기)를 3분기로 싣지 않게(봇 P2).
- **결산월**: 회사 단위로 사업보고서가 정한다 — 목록(소급 400일)에 12월 사업보고서만 있어야 12월 결산으로 보고 그 회사의 정기보고서를 계획한다. 비12월 사업보고서가 있으면 `non_december_fiscal_year`, 사업보고서가 목록에 없으면(창 안 분기보고서의 다음 해 목록을 한 번 더 받은 뒤에도) `fiscal_calendar_unconfirmed` 로 그 회사 전체를 거부한다 — 6월 결산 회사의 9월 분기(=1분기)를 3분기로 싣지 않게(봇 P2).
- **재무 대상 종목**: 접수일 창 [from, to]에 유효했던 구성종목 스냅샷(from 시점 유효 스냅샷 + 창 안 스냅샷)의 합집합.
  from 이전 스냅샷이 없으면 raw manifest `holdings_coverage.uncovered_before`로 드러내고 추정하지 않는다. 우선주 등
  corpCode에 없는 종목은 `unmapped`로 남는다(DART 공시 주체가 아님).
- **재시도**: 공급자 일시 오류는 HTTP 클라이언트(5xx·네트워크 3회). 4xx·DART 키/한도/점검(010·011·012·020·800·901)은 즉시 중단.
  Airflow는 업무 미시작(exit 75)만 재시도. 정제·적재 재시도·재처리(`reprocess_slot`)는 raw·artifact만 읽는다.
- **호출 한도**: 벤더마다 따로다. KIS 공유 예산(ADR-0055, 현재 비활성)은 KIS API 전용이고 KIS 마스터 다운로드는 대상이
  아니다. DART는 공시 레인과 같은 키의 일 한도를 나눠 쓴다(요청 간격 0.5초). FRED 는 별도 키(`edge-dev-data-pipeline/fred/api-key`, 수동 등록)로 계열당 요청 1(백필은 10년 창당 1)이다. 매크로는 FMP 를 쓰지 않는다(2026-10-01).

### 10.6 신선도·완전성

- 원장: 레인 `source-daily`(Airflow 전용, SFN 없음) 9작업이 계획·계측된다. 수집 성공 단위는 응답 객체, `empty`는 실패가 아니다.
  `data_status`는 기존 작업들처럼 완전성 집합 배선 전까지 UNKNOWN이다(ALPHA-611 축).
- 신선도: **API 성공·적재 성공은 최신 관측이 있다는 증거가 아니다.** `source_observation_freshness()`가 판정 재료만 나란히
  낸다 — 원장의 마지막 `LOAD_*` FULFILLED 시각·`data_status`(기존 `ops_expected_task` 재사용), 데이터의 마지막 `received_at`,
  최신 관측일과 그 근거(`basis`). 상태는 **항상 UNKNOWN(`NO_PROVIDER_CALENDAR`)** — ECOS는 KRX 휴장일(09-24·25)에도 값을
  냈고(§10.8), DART 접수는 회사마다, KIS 마스터의 공식 게시 캘린더는 미확보라 "어제 값이 있어야 한다"는 기대를 코드가 만들
  근거가 없다. FRESH/STALE 판정은 공급자 캘린더를 둔 뒤 ADR-0043 Dataset Contract로 붙인다. `MACRO_COLLECTION`은
  `macro` task-def 배포(#1036) 뒤 계측으로 올렸다(ALPHA-1140) — 수집 단계의 원장 증거도 남지만, 함수는 적재 작업만 본다.
- 재무 판본 상태: `financial_quarters_as_of` 가 행마다 권위 판본(run·수신시각)과 `latest_unconfirmed_at` 을 준다 — "확정값이 있는데 최근 확인이 실패했다"와 "확인했는데 지표가 없다"(NULL 행)와 "아직 확인 안 됨"(판본 없음)이 갈린다.
- 재무 완전성: `etf_constituent_source_coverage(etf, T)`의 `eps_quarters`<4·`latest_bps_period` 결측이 종목별 부족이다. 이 함수가 고른 구성종목은 v2 런타임이 고르는 것과 다를 수 있다(§10.11).
- 업종 완전성: 같은 함수의 `has_sector_classification=false`.

### 10.7 소비 쪽과 맞출 것 (v2 fixture 계약과의 차이)

| 항목 | v2 fixture 계약 | 이 계약 | 제안 |
|---|---|---|---|
| 매크로 관측 시각 | `observed_at` 오프셋 있는 순간값 필수 | 관측일(DATE)만 있다 | **반영됨** — `common.observed()`가 날짜형을 그 한국 날짜의 끝으로 읽고(`fixture-tool-contract.md` 매크로 절), 어댑터는 관측일 문자열을 그대로 싣는다. 시각을 지어내지 않는다 |
| `available_at` 의미 | "공개·수신시각" | 수신 기준(매크로·업종), 공개일 다음날 00:00과 수신 중 이른 쪽(재무) + `availability_basis` | 판본마다 근거를 싣는다. 과거 fx_daily의 규칙값(다음날 06:00)은 이 정의가 아니다 |
| 재무 `published_at`(#995 `financial_observations`) | 순간값 필수 | 접수일(DATE)만 | 같은 이유로 날짜형을 받거나 `available_at`만 쓴다 |
| Q4 EPS | 해당 분기 EPS | `FY_MINUS_9M` 유도(근사) | 유도는 적재(결정 ②). 소비 정책 적용(§10.4 소비 정책 ②): 계산 결과에 `approximate` 표시, 표시 없는 카드에선 PER 결측 |
| 구성종목 없는 시점 | 불완전 비중 거절 | 0행 | 일치 |

### 10.8 소량 실응답 검증 (2026-09-30, 읽기 전용)

**계획(호출 전에 고정).** 기존 키만 쓴다: FMP·DART = dev 파이프라인 시크릿(`edge-dev-data-pipeline/{fmp,dart}/api-key`),
KIS 마스터 = 인증 없는 공개 파일, ECOS = 공식 문서의 공개 샘플 키(`sample`, 계정 없음). **KOSIS·EIA 는 키가 없어 보류**
(무료 발급이 필요 — 신규 가입이라 이번 범위 밖). 재시도 상한은 어댑터의 HTTP 클라이언트 그대로(5xx·네트워크 3회, 4xx 즉시 중단).
운영 DB·S3·클라우드 리소스는 건드리지 않는다. 응답 원문은 `.dev/alpha-1130-live/`(미추적)에 두고, 구조를 보존한 축약본만 fixture 로 커밋한다.

| 공급자 | 요청 | 예상 호출 | 확인할 것 |
|---|---|---|---|
| FMP | `historical-price-eod/full?symbol=USDKRW` 2026-09-15~26 · `treasury-rates` 같은 기간 | 2 | 계열·관측일·close/year10 의미와 단위, dev `fx_daily`(07-31 이전) 값과 겹치는 날 대조 불가 → 형태만 |
| ECOS(샘플 키) | `817Y002/D/…/010210000` 2026-09-01~26 | 1 | ITEM_NAME1 = 국고채(10년), UNIT_NAME, TIME 형식 |
| KIS 마스터 | `kospi_code.mst.zip`·`kosdaq_code.mst.zip`·`idxcode.mst.zip` | 3 | 고정폭 뒷부분 길이(227·221), 업종명 파일 이름 위치(헤더 `[5:45]` vs 샘플 `[3:43]`), `0000` 의 실제 분포 |
| DART | 삼성전자(우선주 있음)·SK하이닉스: `list.json`(정기공시 2025-01-01~2026-09-30) 2 · `fnlttSinglAcntAll` 삼성 2026/11012 CFS·OFS, 2025/11011 CFS, 2025/11014 CFS, 하이닉스 2026/11012 CFS · `stockTotqySttus` 삼성 2026/11012 · 정정본 표본(목록에 `[기재정정]` 이 있으면 그 보고서 1건) | ≤ 10 | 3개월/누적 필드, currency·단위, 연결/별도, EPS 계정 줄(보통주·우선주), 주식총수 행(se), 정정본 접수번호 ↔ 목록 접수일 |

합계 예상 ≤ 16회. 실제 호출 수와 결과는 아래 "실측"에 적는다.

**실측(2026-09-30).** 실제 호출 FMP 2(+USDKRW 402 진단 1) · ECOS 4(817Y002 1/10000 실패 → 1/10 재요청, 항목표 731Y001·731Y003, 731Y003 데이터)
· KIS 3 · DART 13(corpCode.xml 1·목록 4·재무제표 6·주식총수 2 — 계획 ≤10 초과분은 정정본 표본을 찾은 시장 목록 1 + 고려제강 목록·재무제표 2).
합계 23(계획 ≤16). 원문은 `.dev/alpha-1130-live/`(미추적), 구조를 보존한 축약본 17개는 `tests/fixtures/source_observations/live/`
(`test_source_observations_live.py`가 공식 필드 설명과 대조). 운영 DB·S3·클라우드 변경 0.

| 공급자 | 확인된 사실 | 코드에 반영 |
|---|---|---|
| FMP USDKRW | `historical-price-eod/full?symbol=USDKRW` → **HTTP 402** "not available under your current subscription" | 계열 공급자를 ECOS로 교체(아래). FMP FX는 유료 구독 없이는 없다 |
| FMP treasury | `treasury-rates` 2026-09-15~25 9행, `year10` 5.17(09-25), % 단위, `date` YYYY-MM-DD | 그대로 |
| ECOS 817Y002 | `ITEM_NAME1`=국고채(10년), `UNIT_NAME`=연%, `TIME`=YYYYMMDD, 09-15~23 7행(09-24·25 없음). 샘플 키는 **10건 상한**(ERROR-301) — 운영 키는 필요 | 그대로. 파싱은 `Decimal` |
| ECOS 731Y003 | 항목 `0000003` 원/달러(종가 15:30, 1990~) · `0000013` 원/달러(종가, 2024-07~). 09-15~28 **10행 — KRX 휴장일 09-24·25에도 값이 있다**(출처 미확인) | `usd_krw`=`731Y003/D/0000003`, 단위 `KRW_per_USD`, vendor `ecos`(DB CHECK 갱신). 종가 15:30 vs 02:00 종가 선택은 §10.9 ③ |
| KIS 마스터 | 고정폭 뒷부분 227·221 맞음. 업종명 파일 이름 위치는 헤더대로 `[5:45]`(샘플 `[3:43]`은 틀림). **소분류는 전부 0000**. 삼성전자 대 0027(제조)·중 0013(전기·전자). KOSDAQ ST 248종 미분류 | `parse_sector_names` `[5:45]`. 소분류 NULL이 정상값이라 "분류 없음"과 구분 안 됨 — 소비자는 대·중만 신뢰 |
| DART 재무제표 | `thstrm_amount`=해당 3개월(분기·반기 보고서), `thstrm_add_amount`=누적. `currency` KRW. 삼성 IS 주당 계정은 `thstrm_amount` 없이 CIS에 있음, SK하이닉스는 CIS `기본주당반기순이익` 한 줄만 | `_pick_line` IS→CIS 순서, 누적/3개월 필드 분리, KRW 외 거부 |
| DART 주식총수 | 삼성 `se` 보통주 5,846,278,608 · 우선주 802,371,203 · 자기주식 82,086,705, `stlm_dt` 2026-06-30. 하이닉스 우선주 `-` | `_share_count` `-`→0. 우선주>0이면 보통주 BPS 차단(§10.9 ①) |
| DART 정정본 | 고려제강 반기(2026.06): 목록에 `20260814004051`[첨부추가]·`20260929000540`[기재정정] 둘, **API는 최신 `20260929000540`만** 반환. 접수일 09-29 → 가시 09-30 00:00 | 정정본 값은 정정 접수일로 보인다 — `finish()`가 재무 API 가 돌려준 `rcept_no` 로 목록 접수일을 찾는다(`test_dart_live_correction_receipt_is_the_one_the_api_returns`). 원본 접수일에 붙이지 않고, 정정 전 값은 복원 불가 |
| KOSIS·EIA | 1회차엔 키가 없어 미검증 → **2회차(아래)에서 검증** | — |

**2회차 실측(2026-09-30 오후, 발급 키 — 실행기가 외부 요청 시도 20회 상한을 강제, 실제 7회).** ECOS 2(국고채·USD/KRW 15:30,
발급 키 정상) · KOSIS 2(T03 데이터 2026-06~08 + 공식 항목 메타 getMeta ITM) · EIA 1(RBRTE 2026-09) · DART 2(사업보고서 11011 주식총수
삼성·하이닉스). FMP treasury·KIS 마스터는 다시 부르지 않았다. 원문 `.dev/alpha-1130-live/round2/`(SHA256SUMS·README, 키 스캔 0건).

**추기 — 미 국채 10년 공급자 교체(2026-10-01, ALPHA-1136).** FMP 를 쓰지 않기로 해 `us_10y_yield` 를 FRED `DGS10` 으로 바꿨다.
같은 계열인지 공식 원문으로 대조했다(FRED 페이지 1회·API 문서 1회 조회, 나머지는 연준·재무부 문서):

| 항목 | FMP `treasury-rates` `year10`(교체 전) | FRED `DGS10` |
|---|---|---|
| 원천 | 재무부 일별 par yield curve 10년(이 문서·어댑터의 기존 서술. FMP 문서 페이지는 403 으로 재확인 못 함) | 연준 H.15 "Treasury constant maturities" — H.15 각주: Source U.S. Treasury, "재무부가 일별 수익률곡선에서 고정 만기로 읽은 값" |
| 산출 | par yield curve 를 10년 만기에서 읽은 값 | 재무부 FAQ: "CMT yields are read directly from the Treasury's daily par yield curve" — 같은 곡선, 같은 만기 |
| 단위 | % | % (Percent, Not Seasonally Adjusted) |
| 주기·날짜 | 미국 영업일, `date` YYYY-MM-DD | Daily, `date` YYYY-MM-DD. 휴일·미게시일은 값 `"."` |

판단: **같은 의미 — `us_10y_yield`·단위 `percent` 를 유지**하고 공급자 `fred`·원천 계열 `FRED DGS10` 만 바꾼다(DB CHECK 은
확장 마이그레이션 `V202610011500` 이 FRED 튜플을 더하고 FMP 튜플은 남긴다). 이전 보고의 "FRED DGS10 은 par yield 와 정의가
다르다"는 **틀렸다** — 시리즈 제목의 'Market Yield … Constant Maturity' 는 H.15 의 명칭일 뿐 값은 재무부 par yield curve 다.
**값 대조(실응답, 2026-10-01 FRED 호출 1회 — 문서 조회 2회 포함 누적 3/5)**: FRED `DGS10` 09-15~29 11행을 받아 교체 전 FMP `year10`
실응답(09-15~25 9행)과 겹치는 9일을 비교했다 — **9일 전부 같다**(09-25 5.17 포함). FRED 는 값을 자릿수 그대로 준다(`"5"`).
원문은 fixture `live/fred_dgs10.json`(키 없음 확인).
가시시각은 그대로 수신시각이다 — FRED 의 `realtime_start` 를 공개시각으로 쓰지 않는다(결정 ①).

| 공급자 | 확인된 사실 | 코드에 반영 |
|---|---|---|
| KOSIS T03 | 공식 메타: T03=`전년동월비(%)`·"Change over the same month of last year", C1 0=총지수. **데이터 행·메타 모두 `UNIT_NM` 없음**(T02 전월비만 `%`) — 단위는 항목명 끝 "(%)" 에만 있다. 2026-06 3.2·07 2.8·08 3.1, 09 은 미공표 | 합성 fixture 가 지어낸 `UNIT_NM` 때문에 파서가 실응답 전건을 `unit_mismatch` 로 거부하던 것을 고쳤다: `UNIT_NM` 이 있으면 그것, 없으면 항목명 "(%)" 만 단위 증거(둘 다 없으면 거부) |
| EIA RBRTE | `series-description`=Europe Brent Spot Price FOB (Dollars per Barrel), `units`=`$/BBL`, daily, 09-30 조회 시 최신 09-22(약 1주 지연) | 그대로. 합성 fixture 를 실응답 구조·값으로 교체 |
| ECOS(발급 키) | 국고채 09-15~29 9행(추석 09-24·25 없음), USD/KRW 15:30 11행(휴장일에도 있음) | 그대로 |
| DART 사업보고서 주식총수 | 삼성·하이닉스 모두 `stlm_dt`=2025-12-31 | 기준일=보고기간 말 검사가 연간 표본에서도 맞다 |

실응답에 **우선주 EPS 줄은 없었다**(세 회사 모두 기본·희석 한 줄씩). 우선주 EPS 후보 선택은 합성 표본으로만 검증했다
(`test_preferred_share_eps_candidates_through_extract_synthetic` — 우선주 줄만 있으면 거부하도록 이번에 고쳤다).

### 10.9 결정·합의 현황 (2026-09-30 기준 — 아래 표는 결정 이력, 현재 상태는 이 문단)

**현재**: ① 우선주 회사 보통주 BPS 차단 유지 · ② Q4 EPS 유도값 보존, 근사 표시가 보장되는 경로에서만 사용(카드는 PER 제외) ·
③ USD/KRW ECOS 15:30 · ④ API 키 발급·운영 시크릿 연결 완료(2026-10-01 `macro:1`, #1036) · ⑥ 지표 0개 정정 판본 구현·검증으로 해결 —
여기까지 **결정 완료**. **남은 합의는 ⑤ ALPHA-643 재사용 하나**(담당자 합의 대기, 이 트랙의 배포·첫 수집을 막지 않는다).

| # | 쟁점 | 선택지 | 영향 |
|---|---|---|---|
| ① BPS 분모 — **A 적용(2026-09-30 지시)** | 우선주가 있는 회사(삼성전자 — 091160 최대 비중)의 BPS 를 무엇으로 두나. 코드: 보통주 기준 `bps` 는 우선주 0인 회사만, 통상 관행(보통주+우선주) `bps_total_shares` 는 근거와 함께 보존만, 우선주 회사는 `bps_note=PREFERRED_SHARES_PRESENT` 로 **계산 차단**·가중 PBR 은 전체 결측(부분 커버리지 표시 없음). B·C 로 바꾸려면 팀 결정 | A. 지금대로(우선주 회사는 PBR 결측 → `weighted_pbr` 카드 전체 결측 — 가중 계산은 결측 종목을 빼지 않는다) · B. `bps_total_shares` 를 PBR 분모로 허용(관행. 보통주 가격 ÷ 전체 주식 기준 BPS — 분모 주식수가 늘어 BPS 가 작아지므로 삼성(우선주 12%)은 보통주 기준보다 PBR 이 약 14% **높게** 나온다) · C. 우선주 자본을 분리해 보통주 기준 계산(DART 단일계정에 우선주 자본 항목 없음 — 원천 추가 필요) | A는 정직하되 KODEX 반도체 PBR 카드가 안 나온다. B는 한 줄(어댑터가 `bps_total_shares` 를 `bps` 로 넘김)이나 문구에 "전체 주식 기준"을 달아야 한다. C는 이 트랙 밖 |
| ② Q4 EPS 유도값 사용 — **적용(2026-09-30 지시)**: 유도값 보존 + 결과에 `approximate` 전달, 표시 못 하는 카드(`weighted_per`)는 결측 | `FY_MINUS_9M` 은 가중평균 주식수 차이로 근사다. 남은 선택은 카드 UI 가 근사 표시를 갖출 때 PER 카드를 되살릴지 | A. 그대로 합산(근사 허용, 카드에 "Q4 유도" 표시) · B. 유도 분기가 4분기 안에 있으면 TTM PER 결측 · C. 유도 분기가 있으면 카드에 각주만 | 12월 결산 회사는 매년 3~5월 사이 Q4 유도가 TTM 에 반드시 들어간다 — B 는 그 기간 PER 전멸 |
| ③ USD/KRW 계열 — **15:30 확정(2026-09-30 지시)** | ECOS `0000003`(종가 15:30, 장기 이력) vs `0000013`(종가 — 야간 거래 포함 02:00, 2024-07~) — 전환·혼합 안 함 | 15:30 은 국내 장 마감 기준으로 주가와 시각이 맞다. 02:00 은 더 늦은 정보지만 2024-07 이전 이력이 없다 | 지금 `0000003`. 바꾸면 계열 정의(`_ECOS`) 한 줄 + 기존 행 없음(미수집)이라 이력 충돌 없음 |
| ④ KOSIS·EIA·ECOS 키 — **발급됨(2026-09-30, 로컬 검증에 사용)** | 운영 보관 위치는 미정 | `edge-dev-data-pipeline/{ecos,kosis,eia}/api-key` 시크릿 신설(Airflow 담당) | 시크릿·taskdef 전까지 `macro` 스텝은 운영에서 못 돈다 |
| ⑥ 전 지표 거부 판본 — **해소(A 적용, 2026-09-30)** | 정정 실행이 한 보고서의 지표를 하나도 만들지 못하면 옛 값이 최신처럼 남던 결함 | `financial_report_version` 표(§10.4): 정제가 보고서·실행마다 판본을 만들고 같은 트랜잭션으로 적재, 조회는 확정 판본 단위. 선택 이유: 거부는 행이 아니라서 지표 표만으로는 표현 불가 → 판본 사실을 지표와 독립으로 두되 **같은 정제·같은 manifest·같은 트랜잭션**에 묶어 새 파이프라인 단계·새 소비 마커를 만들지 않았다(B 의 적재 실패 게이트는 "옛 값이 보인다"를 못 없앤다) | 검증: 실 PG e2e(지표 0개·일부·공급자 실패 vs 013·늦은 옛 실행·중단 복구·중복)와 v2 통합(미확정 확인 노출, 정정 공개 전후) |
| ⑤ ALPHA-643 겹침 — 담당자 미확정(합의 없음, 2026-09-30 재확인: 여전히 '해야 할 일'·코드 0) | ALPHA-643(해야 할 일, 코드 0줄)의 `security_fundamental_quarterly`(revenue·cost_of_sales·sga, `available_date`, `revision_ord`)와 이 PR 의 `financial_metric`(eps·bps·revenue·operating_income, `available_at`, `raw_run_id` 판본) | A. **재사용** — 643 이 `financial_metric` 에 지표 행(`cost_of_sales`·`sga`)만 CHECK 로 추가하고 `segment_revenue`·`analyst_estimate` 는 별도 표 · B. 643 설계대로 별도 표(같은 DART 원천을 두 표에 두 번 정제) · C. 병합 후 643 폐기 | A 권장: 643 의 PIT 요구(`rcept_dt`→가시일·정정 append-only)는 `available_at`·`rcept_no`·판본 행이 이미 충족. 643 완료 조건(코스피200 95%·연결/별도 혼용 0)은 그대로 643 몫. 다른 사람 작업은 지우지 않는다 — 643 본문에 선택지만 남긴다 |

### 10.10 과거 평가 입력의 확보 범위

평가일은 v2 재생 계약이 쓰는 **2026-09-14~18**(`fixture-tool-contract.md` "시간순 재생")을 **가정**했다 — 실제 평가 기간은 확정되지 않았다(확정되면 표만 다시 계산). "함수 구현"과 "입력 확보"는 다르다: 아래 다섯 함수는 모두 구현·로컬 검증됐다. dev DB 에는 2026-10-02 첫 소량 수집분만 있다(매크로 2계열 09-15~25, 업종 10-02, 재무는 심텍 2026-Q2 한 건). 아래 표는 그대로 수집 계획의 근거다.

| 데이터셋 | 평가에 필요한 범위 | 공급자 이력 | 계약대로 보이는 범위(수집 뒤) | 결손과 소비자가 보는 모양 |
|---|---|---|---|---|
| USD/KRW·국고채 10y | 9-14~18 각 시점의 최근 2관측 → 09-10~17 관측일 | ECOS 1990~·1995~ 전량 | 백필 값은 **수신시각(수집일) 이후**에만 보인다(결정 ①). 평가시각 T=09-14~18 < 수집일이면 `macro_observations_as_of(T)` 는 **0행** | `macro_inputs` gap `no_observation_visible` — 결손으로 드러난다("현재 지식으로 평가" 모드는 만들지 않기로 했다, 2026-09-30) |
| 미국채 10y | 같음 | FRED DGS10 1962~(교체 전엔 FMP treasury) | 같음 | 같음 |
| CPI YoY | 8월분(9월 초 공표) | KOSIS 이력 있음(키 발급·운영 시크릿 연결 완료(2026-10-01)) | 같은 수신시각 규칙 | 같음 |
| 브렌트 | 09-10~17 | EIA 이력 있음(키 발급·운영 시크릿 연결 완료(2026-10-01)) | 같음 | 같음 |
| 재무(EPS·BPS) | 구성종목별 최근 4분기(2025-Q3~2026-Q2) | DART 최신 제출본만. 2026-Q2 반기보고서 접수 08-14 | 백필해도 **접수일 기준으로 과거에 보인다**(결정 ①): T=09-14 에 2026-Q2 까지 보인다. 단 09-29 정정본(고려제강 등 49건/955 중)은 정정 값이 원본 접수일에 붙지 않으므로 **T=09-14 에는 그 회사의 2026-Q2 가 없다**(정정 전 값은 API 가 안 준다) | `financial_inputs` 는 그 분기 행을 안 낸다(공개 자체가 없음) → `valuation.calculate` "four consecutive released quarters required". 우선주 회사는 ① 전까지 gap `PREFERRED_SHARES_PRESENT` |
| 업종 | 09-14~18 구성종목의 대·중 분류 | KIS 마스터 **현재값만** | 첫 수집일 이후만. **09-14~18 시점의 업종은 없다**(복원 주장 안 함) | `sector_classification_as_of(T)` `found=false` 전건 — 결손으로 드러난다(현재 분류를 소급 적용하지 않는다) |
| 구성종목 | 09-14~18 각 날의 스냅샷 | 기존 `etf_holding_snapshot` | 기존 표 그대로(`etf_constituent_source_coverage`) | 스냅샷 없는 날 0행 |

요약: 과거 평가는 **재무만** 계약 안에서 과거 가시성이 복원되고, 매크로·업종은 수집 시작 이후부터다. "당시 이용 가능한 입력" 계약을
유지하고 "현재 지식으로 과거 평가" 모드는 **만들지 않는다**(2026-09-30 결정) — 2026-09 평가에서 매크로·업종이 비는 것은 결손으로 그대로 드러난다.

### 10.11 구성종목 스냅샷 선택 — 결정·범위·한계·제안 (ALPHA-1139)

2026-10-02 첫 소량 실행에서 `etf_constituent_source_coverage('0210A0', 2026-08-14 00:00 KST)` 가 0행이었다(재무는 S3 스냅샷 08-13 으로 수집됨). 아래는 그 조사 결과를 상태별로 나눈 것이다. **커버리지 함수의 결과는 v2 실행 가능 여부를 보장하지 않는다** — 두 쪽이 스냅샷을 고르는 규칙이 다르다(아래 "팀 합의 필요").

| 구분 | 내용 |
|---|---|
| 결정 완료(2026-10-02) | ① 과거 holdings 를 DB 에 **추가 적재하지 않는다.** S3 canonical 에만 있는 07-15~08-27 스냅샷은 그대로 둔다. ② 구성종목 조회의 시점 조건은 **`available_at` 만 쓴다 — `loaded_at` 을 조회 조건에 넣지 않는다.** 커버리지 함수에 `loaded_at` 조건을 더하지 않는다(지금 코드 그대로). 적재 시점까지 제한해야 한다는 요구가 생기면 그때 더한다. ③ `loaded_at` **컬럼과 기록은 유지한다** — 조회 필터에서만 빼는 것이고, 적재 이력이나 데이터를 지우지 않는다. ④ `available_at`(수집 시각)을 과거로 바꾸지 않는다 |
| 현재 데이터 범위(dev, 2026-10-02 15:40 조회) | DB `etf_holding_snapshot_status` 는 25거래일이다. 정기 적재분은 **08-28 부터**(보통 거래일 당일 15:41 KST 쯤 수집). 그 밖에 재수집된 날짜가 있다 — 08-04·08-10·09-04·09-08·09-09·09-17·09-28 은 S3 canonical 자체가 09-29 에 다시 수집돼 `available_at` 이 09-29 이고, 09-07 은 09-10, 09-23 은 09-24 다. S3 canonical 은 07-15 부터 있다 |
| 유지할 동작 | 기준시각에 볼 수 있는 스냅샷이 없으면 **최신 구성으로 대신하지 않는다.** 커버리지 함수는 0행, v2 는 `No holdings snapshot available at analysis time` 으로 실패, 재무 수집은 raw manifest 에 `no_holdings_snapshot` 을 남긴다 |
| 남은 한계(미해결, 수정 보류) | 커버리지 함수의 0행은 사유를 구분하지 못한다: ① 모르는 ETF ② 기준시각에 볼 수 있는 스냅샷 없음 ③ 유효 구성종목이 없는 스냅샷(유효 행이 입력의 절반 미만이면 good 이 아니라 건너뛴다). 지금은 점검 스크립트(`analysis-engine-v2/scripts/inspect_source_readiness.sql`)만 이 함수를 부르고 운영 런타임은 쓰지 않아 수정을 보류했다. 해결된 것이 아니다 |
| 팀 합의 필요(미결 — 제안 단계) | v2 런타임에는 지금 `loaded_at ≤ T` 필터가 있다. 결정 ②에 맞추려면 분석엔진 코드를 바꿔야 하고, 그 변경은 분석 담당과 합의한 뒤에 한다. 아래 "v2 최소 변경안"은 **제안**이다 — 합의·머지·배포되지 않았다 |
| 이번 결정으로 해결되지 않는 것 | ① 커버리지 0행의 사유 구분(위 "남은 한계") ② 품질 조건의 차이(절반 미만이면 옛 스냅샷 vs 임계 없음) ③ 시점 조건의 적용 단위(스냅샷에 보이는 행이 하나라도 있으면 전체 vs 행마다) ④ v2 의 status 최근 40건 탐색 한도 ⑤ 같은 날짜 재적재의 덮어쓰기 — holdings 표는 ETF·날짜당 한 판본이라, 같은 날짜를 다시 수집해 적재하면 기존 행이 덮인다(로더의 코드 동작). **holdings 원천은 정정본을 주지 않는다** — 정정 수신을 전제로 한 대응·검증은 이 범위에 없다. 모두 별도 문제로 남긴다 |

`loaded_at` 을 조회 조건에서 빼면 **"그 시각에 우리 DB 에 실제로 있었던 데이터만 조회한다"는 보장은 하지 않는다.** 보장하는 것은 "그 시각까지 수집된 데이터"다 — 수집과 적재 사이에는 DB 에 아직 없던 스냅샷이 과거 조회에서 보인다.

**선택 규칙의 차이(코드 기준):**

| | 재무 수집 `constituents_between` | 커버리지 함수 | v2 런타임 `sources/database.py` + `fixture_data/common.holdings`(현행 코드) |
|---|---|---|---|
| 원천 | S3 canonical `etf_holdings/as_of_date=` | DB `etf_holding_snapshot(_status)` | 같은 DB |
| 시점 조건 | 없음(창 시작일 이하 최신 + 창 안 전부의 합집합) | 그 스냅샷에 `available_at ≤ T` 인 행이 **하나라도** 있으면 고른다(`EXISTS`). 고른 뒤에는 시점 조건 없이 그 스냅샷의 모든 행을 돌려준다 | status `loaded_at ≤ T` **그리고** 행마다 `available_at ≤ T`. status 가 보이는데 걸러진 유효 행 수가 `valid_row_count` 와 다르면 `Holdings rows and ingestion status disagree` 로 실패한다(적재가 수집 뒤라 평소에는 status 가 보이면 그 행도 모두 보인다) |
| 품질 조건 | 없음 | `2 × valid_row_count ≥ input_row_count` 인 스냅샷만. 아니면 더 옛 스냅샷으로 내려간다 | 임계 없음. **유효 행이 있는** 스냅샷 중 가장 늦은 `as_of_date` 를 쓴다. 비중이 음수이거나 종목이 중복되거나 비중 합이 0 이하·1 초과면 실패한다. 그 안에서 불완전하면(합이 1 미만이거나 입력 행 수와 다르면) `coverage=partial`(완전성을 요구하는 툴은 실패). 유효 행이 0개인 날짜는 행이 없어 그 전 스냅샷으로 내려간다. 단 v2 는 `loaded_at ≤ T` 인 status 를 **최근 40건**까지만 읽는다 — 그 안에 유효 행이 있는 스냅샷이 없으면 실패한다(커버리지 함수는 한도 없이 내려간다) |
| ETF 식별 | 설정의 ETF 코드 | `instrument.ticker`, 시장 XKRX·XKOS | `ticker` + XKRX + `instrument_type='ETF'` 가 정확히 1건, 아니면 실패 |

- `available_at` 은 S3 canonical 행의 `fetched_at`(수집 시각)이다. `loaded_at` 은 `load-etf-holdings` 가 status 행을 쓴 시각이다. 로더는 같은 (ETF, 날짜)를 다시 적재할 때마다 `loaded_at = now()` 로 덮어쓴다(값이 같아도).
- **실측(dev 25거래일 전부):** `loaded_at − available_at` 은 159~675초다. 차이가 0인 날은 없다. good 조건에 걸린 스냅샷은 없었다(품질 조건 차이는 코드상 차이이고 데이터에서 관측되지는 않았다).

**시간 경계 예시(현행 코드 기준 — 커버리지 함수와 v2 가 지금 어떻게 갈리는가):**

| # | 상황 | 기준시각 T | 커버리지 함수가 고르는 스냅샷 | v2 가 고르는 스냅샷 | 근거 |
|---|---|---|---|---|---|
| 1 | 매 거래일의 수집~적재 사이 | 2026-10-01 15:45 KST | 10-01(`available_at` 15:41:25) | 09-30(10-01 은 `loaded_at` 15:52:00 이라 아직 안 보임) | 실측. 매일 3~11분 |
| 2 | 적재 지연·실패 뒤 복구 | D일 15:41 수집, 적재는 D+1일 10:00 에 복구 → T = D일 18:00 | D | D−1 | 코드. 창이 복구 시각까지 늘어난다(관측 사례 없음) |
| 3 | 같은 스냅샷 재적재 | 10-01 스냅샷을 10-03 10:00 에 다시 적재 → T = 10-02 09:00 | 10-01(`available_at` 그대로) | 09-30(`loaded_at` 이 10-03 으로 바뀌어 T 뒤가 됨) | 코드. **재적재가 과거 T 의 v2 결과를 바꾼다**(관측 사례 없음) |
| 4 | 유효 행이 1개 이상이지만 입력의 절반 미만인 스냅샷 | 그 스냅샷이 보이는 T | 건너뛰고 그 전 스냅샷 | 그 스냅샷 — 비중 합이 0 초과·1 이하면 `partial`(완전성을 요구하는 툴은 실패), 아니면 실패 | 코드(관측 사례 없음). 유효 행이 0개면 두 쪽 모두 그 전 스냅샷으로 내려간다(v2 는 최근 status 40건 안에서만) |
| 6 | 한 스냅샷 안에서 행마다 수집 시각이 다른 경우(예: 15:41·15:43 수집, 15:50 적재) | 15:42 — 일부 행만 `available_at ≤ T` | 그 스냅샷의 **모든** 행(아직 수집 전인 행 포함) | 그 전 스냅샷(이 스냅샷은 `loaded_at` 15:50 이라 안 보임. 그 전 것도 없으면 `No holdings snapshot available at analysis time`) | 코드(관측 사례 없음 — dev 25거래일은 날짜마다 수집 시각이 한 값이다) |
| 5 | 늦게 수집된 날짜 | 09-17 스냅샷(`available_at` 09-29) → T = 09-18 10:00 | 09-16 | 09-16 | 실측. 두 쪽이 같다 — 거래일이 아니라 수집 시각이 가시성을 정한다 |

**v2 최소 변경안(제안 — 분석 담당 합의 전, 미적용):**

- **현재 필터 위치와 동작**: `src/apps/cloud/analysis-engine-v2/src/edge_analysis_v2/sources/database.py` `load_source` 의 status 조회(`… AND trade_date<=%s AND loaded_at<=%s ORDER BY trade_date DESC LIMIT 40`). status 의 `loaded_at` 이 T 이후면 그 스냅샷을 통째로 건너뛴다. 그 뒤 행은 `available_at ≤ T` 로 다시 거르고, 유효 행 수가 `valid_row_count` 와 다르면 `Holdings rows and ingestion status disagree` 로 실패한다.
- **조건만 지우면 안 된다**: status 는 날짜만으로 보이는데 행은 아직 안 보이는 시각이 생겨 위 불일치 실패가 난다(아래 표 1b·3·5).
- **제안**: `loaded_at<=%s` 를 "그 스냅샷(같은 `data_version`)에 `available_at > T` 인 행이 없다"로 바꾼다. 스냅샷이 보이는 시각이 적재 시각에서 **마지막 행의 수집 시각**으로 옮겨진다. 아래 로컬 확인의 여덟 경우에서는 새 실패가 없었다(행이 없는 status 는 예외 — 아래 "달라지는 것"). 한 곳의 SQL 만 바뀐다:

  ```diff
  -    statuses = _rows(connection, """SELECT trade_date,input_row_count,valid_row_count,data_version
  -        FROM etf_holding_snapshot_status WHERE etf_instrument_id=%s
  -        AND trade_date<=%s AND loaded_at<=%s ORDER BY trade_date DESC LIMIT 40""", (etf['instrument_id'],at.date(),at))
  +    statuses = _rows(connection, """SELECT s.trade_date,s.input_row_count,s.valid_row_count,s.data_version
  +        FROM etf_holding_snapshot_status s WHERE s.etf_instrument_id=%s AND s.trade_date<=%s
  +        AND NOT EXISTS (SELECT 1 FROM etf_holding_snapshot h WHERE h.etf_instrument_id=s.etf_instrument_id
  +            AND h.trade_date=s.trade_date AND h.data_version=s.data_version AND h.available_at>%s)
  +        ORDER BY s.trade_date DESC LIMIT 40""", (etf['instrument_id'],at.date(),at))
  ```

- **유지되는 것**: 행 단위 `available_at ≤ T` 필터, `valid_row_count` 불일치 실패, 스냅샷이 없을 때의 `No holdings snapshot available at analysis time`, 최신 구성으로 대신하지 않는 동작, `partial` 판정, status 최근 40건 한도. `loaded_at` 컬럼과 로더의 기록은 그대로다.
- **달라지는 것**: 수집과 적재 사이의 시각에서 당일 스냅샷이 보인다. 같은 데이터를 다시 적재해도 과거 조회가 흔들리지 않는다(`available_at` 이 그대로라서). 행이 하나도 없는 status 는 적재 시각이 아니라 거래일부터 보인다. 행이 없어 그 날짜가 선택되지는 않지만 **최근 40건 한도는 차지한다** — 그런 status 가 40건 이상 이어지면 그 앞의 유효 스냅샷이 탐색에서 밀려 실패한다(현행은 적재 시각 뒤에만 그렇다). dev 25거래일에는 행이 없는 status 가 없다(관측 사례 없음).

**로컬 경계 확인(2026-10-02, 임시 테이블·공급자 호출 없음·운영 데이터 접근 없음).** 전날 스냅샷(09-29, 비중 0.6·0.4)과 당일 스냅샷(09-30, 비중 0.5·0.5)을 두고 세 변형이 고른 스냅샷:

| # | 상황 | 기준시각 T | 현행(`loaded_at`) | 조건만 제거 | 제안 |
|---|---|---|---|---|---|
| 1 | 수집 15:41:25 ~ 적재 15:52:00 사이 | 09-30 15:45 | 09-29 | 09-30 | **09-30** |
| 1b | 당일 수집 전 시각을 나중에 재생 | 09-30 10:00 | 09-29 | 실패(불일치) | 09-29 |
| 2 | 같은 데이터를 10-02 10:00 에 다시 적재 | 10-01 09:00 | 09-29 | 09-30 | **09-30** |
| 3 | 같은 날짜를 10-02 10:00 에 다시 수집해 적재(기존 행이 덮임) — 재수집 전 시점 | 10-01 09:00 | 09-29 | 실패(불일치) | 09-29 |
| 3b | 같은 재수집 — 재수집 뒤 시점 | 10-02 12:00 | 09-30 | 09-30 | 09-30 |
| 4 | 볼 수 있는 스냅샷 없음 | 09-28 10:00 | 실패(스냅샷 없음) | 같음 | 같음 |
| 5 | 행마다 수집 시각이 다름(15:41·15:43, 적재 15:50) | 09-30 15:42 | 09-29 | 실패(불일치) | 09-29 |
| 6 | 평소(적재 뒤) | 09-30 18:00 | 09-30 | 09-30 | 09-30 |

- 현행과 제안이 다른 것은 1·2 뿐이다(굵게). 패치를 적용한 상태에서 v2 단위 테스트와 `integration_tests/test_source_queries.py` 는 모두 통과했다(376 passed). 패치·확인 스크립트·출력은 레포 밖 증거 폴더(`~/Desktop/Development/edge/.dev/alpha-1136-first-run/followup/`)에 있다. 레포에는 반영하지 않았다.
- **이 제안이 해결하지 않는 것**: 3번처럼 같은 날짜를 다시 수집해 적재하면 기존 행이 덮이고 `available_at` 이 새 수집 시각이 된다. 그러면 재수집 전 시점의 조회는 그 날짜를 건너뛰고 전날 구성을 쓴다 — 재수집 전에 실제로 보던 구성과 다르다. 현행도 같다. **재적재에 따른 과거 조회 변화가 해결된 것이 아니다** — 수집 시각이 그대로인 재적재(2번)만 흔들리지 않게 될 뿐이다. 두 가지를 구분한다: ① **코드 동작** — 로더는 같은 (ETF, 날짜)를 다시 적재하면 기존 행을 덮는다. ② **데이터 확보** — holdings 원천은 정정본을 주지 않으므로 '정정 데이터'가 들어오는 경로는 없다. dev 에서 실제로 있었던 것은 과거 날짜의 재수집이다(08-04·08-10 등이 09-29 에 다시 수집돼 `available_at` 이 09-29 가 됐다).
- 커버리지 함수는 "보이는 행이 하나라도 있으면" 고르므로, 5번 같은 경우에는 제안을 적용해도 v2 와 다르게 고른다(커버리지 09-30, v2 09-29). 적용 단위 차이는 위 "해결되지 않는 것 ③"으로 남는다.

**분석 담당과 따로 정할 것(이번 결정 밖):**

1. 품질 조건 — 유효 행이 절반 미만이면 옛 스냅샷으로 내려갈지(커버리지), 유효 행이 하나라도 있으면 최신을 쓰고 `partial`(비중 합이 0 초과·1 이하일 때) 또는 실패로 드러낼지(v2).
2. 시점 조건의 적용 단위 — 스냅샷 단위(한 행이라도 보이면 전체)인지 행 단위인지(예시 6).
3. 커버리지 0행의 사유 구분이 필요한 소비자가 점검 스크립트 말고 있는가. 있다면 반환 모양을 바꿀지, 모르는 ETF 만 오류로 올릴지.
4. v2 의 status 최근 40건 한도를 커버리지 함수에도 둘지, v2 에서 뺄지.
5. 같은 날짜 재수집·재적재가 과거 시점 조회를 바꾸는 것을 받아들일지, 판본을 보존할지(holdings 표 구조 변경이 필요하다). 정정본 수신은 전제하지 않는다.

커버리지 함수를 고치게 되면 마이그레이션 하나로 묶는다. 이 항목들과 무관하게 수집·원장 연결(ALPHA-1140)은 진행할 수 있다. 수집 대상은 창 시작일 이하 최신 S3 스냅샷과 창 안 스냅샷의 합집합이라, 정기 14일 창에서는 소비가 고르는 스냅샷이 대개 그 안에 든다. 항상은 아니다 — 소비가 창 시작일보다 오래된 스냅샷을 보는 동안, 그 사이 구성에서 빠진 종목이 그날 낸 정기보고서는 그날 run 에서 받지 않는다(조건이 겹쳐야 하고 관측 사례는 없다).
