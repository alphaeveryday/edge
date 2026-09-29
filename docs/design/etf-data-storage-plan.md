# ETF 데이터 저장 경로 설계 초안

상태: 팀 검토용 제안 · 2026-09-28. 저장소 코드·문서 기준이며 S3/RDB 실측, 신규 수집기·테이블 구현은 하지 않았다.
예외: §10(분석 v2 원천 관측 — 매크로·재무·KIS 지수업종)은 2026-09-30 코드·로컬 검증까지 구현했다(미배포·미수집).

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

상태: **구현(코드·로컬 검증) — 미배포·미수집·정기 비활성.** §9를 적용한 첫 데이터셋 셋이다. 입력 요구는
"v2 필요 데이터와 확보 현황"(2026-09-26 조사): KODEX 반도체(091160)와 **각 분석 시점의** 전체 구성종목, 매크로 5계열.
결정(2026-09-30 확인): ① 과거 가시시각은 공급자 공개일 증거가 있는 자료(DART 접수일)만 재구성하고 매크로 백필은 수신시각 기준,
② 재무 Q4 = FY − 9개월 누적(유도 표시), ③ 연결 우선·연결 재무제표가 없는 회사만 별도.

코드: 공급자 해석 `sources/macro_series.py`·`dart_fundamental.py`·`kis_sector_master.py`, 저장·계보 `steps/source_observations.py`,
경로 빌더 `lake/storage.py`, DB `V202609301200__add_source_observations.sql`, DAG `airflow/dags/edge_source_daily.py`.

### 10.1 기존 표면을 쓰지 않은 이유

| 기존 표면 | 확인한 사실 | 판정 |
|---|---|---|
| `canonical/market_data/fx_daily`·`rates_daily` | [실측 2026-09-30] 레포 밖 일회성 적재(07-31 정지). `available_at`이 **시간대 없는 규칙값**(fx 다음날 06:00, rates 05:00)이고 수신시각이 없다. 값은 double, rates는 만기별 wide 형식. 레포에 writer·빌더 없음 | 재사용하지 않는다. 같은 열 이름에 다른 시각 정의를 섞게 된다(§9 "이름이 같아도 정의가 다르면 가른다"). v1 소비자(`paneltest`)용 생산자는 여전히 ALPHA-1105 몫이다. USD/KRW 값의 공급자·계열(FMP USDKRW close)은 같다 |
| `draft/canonical/financials/statement_line`(Iceberg) | 임시 존(ADR-0057 §1). writer가 Athena MERGE이고 스케줄 없음·dartlab 파티션만 읽음. `available_at`=접수번호 앞 8자리(목록 `rcept_dt`와 다를 수 있다 — `dart_disclosure` 주석). 주식총수(BPS 분모) 없음 | 재사용하지 않는다. 계정 원문 전체 보존은 raw가 한다 |
| DB `instrument_classification` | FMP 업종 텍스트 2열, PK `(instrument_id, as_of_date)`라 판본·출처를 못 가른다. 실데이터 사실상 빈 표(`tool_peer` 주석) | 재사용하지 않는다. KIS 지수업종은 다른 분류 체계다 |
| `analysis/backfill/sector_member` | pykrx KRX 업종지수 1단계, 분기 스냅샷 | 대·중·소 3단계 KIS 체계와 다르다. 섞지 않는다 |

### 10.2 데이터셋 요약

| | `macro_observation` | `financial_metric` | `sector_classification` |
|---|---|---|---|
| 공급자·계열 | FMP `historical-price-eod/full` USDKRW `close`(1달러당 원, 현물) · FMP `treasury-rates` `year10`(%) · ECOS `817Y002/D/010210000` 국고채 10년(연%) · KOSIS `101/DT_1J22042` `T03` 총지수 전년동월비(%) · EIA `petroleum/pri/spt` `RBRTE` Europe Brent Spot FOB($/bbl) | OpenDART `list.json`(정기공시, 접수일) · `fnlttSinglAcntAll.json`(전체 재무제표, CFS·OFS) · `stockTotqySttus.json`(주식총수) | KIS 공개 마스터 ZIP `kospi_code.mst`·`kosdaq_code.mst`(지수업종 대·중·소 4자리) · `idxcode.mst`(업종명). KIS Open API 아님(키·토큰 없음) |
| 대상 | 5계열. 브렌트는 현물(선물 대체 금지), CPI는 공급자 공표 전년동월비(지수 수준 자체 계산 금지) | 구성종목(ETF 스냅샷에서 **기간별** 파생, §10.5) × 12월 결산 정기보고서. EPS 기본·희석, BPS, 매출액, 영업이익 | KOSPI·KOSDAQ 전 종목(ETF·ETN 포함, `security_group`로 구분) |
| 한 행 | (계열, 관측일)의 한 수집 실행 판본 | (회사, 사업연도, 기간, 지표, 기간 종류, 연결/별도)의 한 수집 실행 판본 | (시장, 종목, 받은 날)의 한 수집 실행 판본 |
| 논리 키 | `series_id, observation_date` | `corp_code, fiscal_year, fiscal_period, metric, period_kind, fs_basis` | `market, instrument_code, as_of_date` |
| DB 판본 키 | 논리 키 + `raw_run_id` | 논리 키 + `raw_run_id` | 논리 키 + `raw_run_id` |
| 단위 | 계열별 고정(DB CHECK): `KRW_per_USD`·`percent`·`USD_per_barrel`. 공급자 10진 문자열 그대로(반올림 없음) | 금액 `KRW`(원, 환산 없음 — `currency`≠KRW 행 거부), 주당 `KRW_per_share`. BPS만 소수 6자리 반올림 | 코드 원문 4자리 |
| 결측·0 | 값 행이 없음 = 미수집·미공표. 공급자 "데이터 없음" 응답은 `empty`(정상 0건)로 raw manifest에 남고, 오류는 `error` | 빈 칸·`-`는 결측(0 아님). 주식총수 표의 `-`만 0(자기주식 없음). 연결 없는 회사의 013은 `empty` | `0000` = 분류 없음 → 코드 NULL, 원문 `raw_*_code`에 보존 |
| 관측 시점 | 일별=관측일, 월별=기준월 1일(`reference_period` YYYY-MM) | `period_end`(분기말·기말), `fiscal_period` Q1~Q4·FY | `as_of_date`=받은 KST 날짜(원천이 기준일을 주지 않는다) |
| 공개시각 | 없음(다섯 공급자 모두 API로 주지 않는다) | `rcept_date`=목록 접수일(날짜만). 시각은 만들지 않는다 | 없음 |
| 수신시각 | `received_at`=raw 요청 시각 | 같음 | 같음 |
| 가시시각 `available_at` | `received_at`(basis `received`만 허용 — DB CHECK) | `provider_release_date`: min(수신, 접수일 다음날 00:00 KST). 목록에서 접수일을 못 찾으면 `received` | `received_at` |
| 정정 선택 | 관측일마다 `available_at ≤ T`인 판본 중 가장 늦게 보인 것 | 같음. DART는 최신 제출본만 주므로 정정 전 원값은 복원하지 않는다 | 가장 늦게 보인 스냅샷 |
| 과거 이력 | 수집 시작 이후만(백필 값은 수신 이후에만 보인다 — 결정 ①) | 백필도 접수일 기준으로 과거에 보인다(결정 ①). 정정 전 값은 없음 | **현재값만.** 받기 전 날짜의 분류는 없다(복원 주장 없음) |

### 10.3 경로·파일·계보

| | raw (원본 형식) | canonical 현재 상태 (Parquet) | 실행별 artifact |
|---|---|---|---|
| 매크로 | `raw/source={fmp,ecos,kosis,eia}/dataset=macro_observation/series_id={id}/ingest_date=/run_id=/{id}-{from}-{to}-{sha16}.json` | `canonical/market_data/macro_observation/series_id=/observation_date=/part-00000.parquet` | `operations_archive/canonical_run_artifacts/dataset=macro_observation/run_id=/report_date={ingest_date}/part-00000.parquet` |
| 재무 | `raw/source=dart/dataset=financial_metric/market=KR/ingest_date=/run_id=/{corp}-{종류}-{sha16}.json` (종류: 목록 `list-pN` · 재무제표 `{연도}-{보고서}-{CFS·OFS}` · 주식총수 `…-shares`) | `canonical/financials/financial_metric/market=KR/period_end=/part-00000.parquet` | 같은 규칙(`dataset=financial_metric`) |
| 업종 | `raw/source=kis/dataset=sector_classification/market={KOSPI,KOSDAQ,KR}/ingest_date=/run_id=/{파일}-{sha16}.zip` | `canonical/reference/sector_classification/market=/as_of_date=/part-00000.parquet` | 같은 규칙 |

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

**보존.** 과거 재현의 근거로 약속하는 것은 **만료가 없는 raw·manifest와 DB 판본 이력**이다. 실행별 artifact는
`canonical_run_artifacts/` 30일 만료(terraform `pipeline/storage.tf`) 아래라 **재처리 캐시**로만 쓴다 — 만료된 실행을
다시 적재하려면 같은 raw로 정제를 다시 돈다(결정적 정규화). 그래서 기존 lifecycle을 바꾸지 않는다. artifact 자체를
장기 보존하려면 새 프리픽스(예: `operations_archive/canonical_run_history/`)를 만료 없이 두는 좁은 변경을 제안한다 — 미결정·미적용.

### 10.4 DB와 소비 조회

테이블: `macro_observation`·`financial_metric`·`sector_classification`(추가 전용 판본). 소비 계약은 아래 함수다
(함수가 판본 선택 규칙의 정본이고, 소비자는 테이블을 직접 거르지 않는다).

| 함수 | 반환 | 규칙 |
|---|---|---|
| `macro_observations_as_of(T, series, n=21)` | 최근 n개 관측(관측일·`reference_period`·값·단위·수신·가시·근거 키) | 관측일마다 `available_at ≤ T` 판본 중 최신. 문서의 "최근 공개 2관측일"은 n=2, 툴 탐색 한도 21과 섞지 않는다 |
| `financial_quarters_as_of(T, instrument_code)` | 분기별 EPS(해당 분기)·BPS(분기말)·매출·영업이익과 각 유도 표시·접수번호·run | 누적값은 돌려주지 않는다. 기준(연결/별도)은 회사 단위로 고정 — T까지 연결이 한 번이라도 보이면 연결 |
| `sector_classification_as_of(T, codes[])` | 종목별 최신 스냅샷의 대·중·소 코드·이름 | `found=false`(그 시점 스냅샷에 없음) ≠ 코드 NULL(원천 `0000`) |
| `etf_constituent_source_coverage(etf, T)` | T에 유효한 구성종목 스냅샷(기존 `etf_holding_snapshot`+status good 판정)의 종목별 업종·재무 확보 여부 | 스냅샷이 없으면 0행 — 현재 구성으로 대신하지 않는다 |

예제(로컬 PostgreSQL 검증, `tests/e2e/test_source_observations_pg.py`):

```sql
-- 사업보고서 접수일 2026-03-10: 당일 23:59 에는 2025-Q4 가 없고, 다음날 00:00 부터 Q4(FY−9M) 가 보인다
SELECT period, eps, eps_derivation FROM financial_quarters_as_of('2026-03-10 23:59+09', '005930');  -- 2025-Q3 만
SELECT period, eps, eps_derivation FROM financial_quarters_as_of('2026-03-11 00:00+09', '005930');  -- + 2025-Q4 1100 FY_MINUS_9M
-- 정정: 새 값은 그 판본의 수신 이후에만, 그 전 기준시각은 옛 값
SELECT observation_date, value FROM macro_observations_as_of(:t, 'usd_krw', 2);
```

**권한**: v2 쓰기 역할(`edge_analysis_v2_writer`)은 자기 테이블 밖 권한이 0이어야 한다(`tests/analysis_v2_writer.sql`).
이 세 테이블·함수의 읽기 권한은 v2 읽기 경로를 만들 때(ALPHA-1096·1097) 정한다 — 이 변경은 GRANT를 하지 않았다.

### 10.5 수집 주기·백필·재시도·writer

- **writer**: 데이터셋·파티션마다 정제 스텝 하나(`normalize-{macro,sector,financial-metric}`). raw는 수집 스텝, DB는 적재 스텝.
- **정기**: DAG `edge_source_daily` 매일 09:10 KST 한 슬롯(근거는 DAG 도크스트링). 매크로 창 = 어제 − 소급일 ~ 어제
  (USD/KRW·금리 14일, CPI 124일, 브렌트 28일 — 늦은 게시·정정 흡수). 재무 창 = 접수일 오늘−14 ~ 오늘. 업종 = 거래일만.
- **백필**: 같은 DAG를 수동 trigger + `macro_from/to`·`financial_from/to`(CLI `--from/--to`). 매크로 `to`≤어제, 재무 `to`≤오늘 —
  미래·진행 중 관측은 스텝이 거부한다. 업종은 현재값만이라 백필 인자가 없다(`ingest-raw-sector`가 `--from/--to` 거부).
  한 run은 1500초 안이어야 한다 — 긴 기간은 1년 단위로 나눈다. **실제 확보 기간은 확정하지 않았다**: 평가 날짜가 정해지면
  그 기간을 인자로 준다(최소 이력: EPS 4분기·BPS 1분기·매출/영업이익 2분기 + 재생 준비기간).
- **재무 대상 종목**: 접수일 창 [from, to]에 유효했던 구성종목 스냅샷(from 시점 유효 스냅샷 + 창 안 스냅샷)의 합집합.
  from 이전 스냅샷이 없으면 raw manifest `holdings_coverage.uncovered_before`로 드러내고 추정하지 않는다. 우선주 등
  corpCode에 없는 종목은 `unmapped`로 남는다(DART 공시 주체가 아님).
- **재시도**: 공급자 일시 오류는 HTTP 클라이언트(5xx·네트워크 3회). 4xx·DART 키/한도/점검(010·011·012·020·800·901)은 즉시 중단.
  Airflow는 업무 미시작(exit 75)만 재시도. 정제·적재 재시도·재처리(`reprocess_slot`)는 raw·artifact만 읽는다.
- **호출 한도**: 벤더마다 따로다. KIS 공유 예산(ADR-0055, 현재 비활성)은 KIS API 전용이고 KIS 마스터 다운로드는 대상이
  아니다. DART는 공시 레인과 같은 키의 일 한도를 나눠 쓴다(요청 간격 0.5초). FMP는 공용키 bandwidth(ALPHA-558) 안이다.

### 10.6 신선도·완전성

- 원장: 레인 `source-daily`(Airflow 전용, SFN 없음) 9작업이 계획·계측된다. 수집 성공 단위는 응답 객체, `empty`는 실패가 아니다.
  `data_status`는 기존 작업들처럼 완전성 집합 배선 전까지 UNKNOWN이다(ALPHA-611 축).
- 매크로 신선도: DB의 계열별 최신 관측일과 기대 지연(USD/KRW·금리 영업일 1~3일, CPI 월 1회, 브렌트 주 1회)을 대조한다 —
  공급자 달력(미국 휴일·EIA 공표일)이 레포에 없어 **판정 코드는 아직 없다**. ADR-0043 Dataset Contract 등록은 그 달력을 둔 뒤다.
- 재무 완전성: `etf_constituent_source_coverage(etf, T)`의 `eps_quarters`<4·`latest_bps_period` 결측이 종목별 부족이다.
- 업종 완전성: 같은 함수의 `has_sector_classification=false`.

### 10.7 소비 쪽과 맞출 것 (v2 fixture 계약과의 차이)

| 항목 | v2 fixture 계약 | 이 계약 | 제안 |
|---|---|---|---|
| 매크로 관측 시각 | `observed_at` 오프셋 있는 순간값 필수 | 관측일(DATE)만 있다 | fixture 문서 스스로 "관측일만 있는 자료를 자정 관측으로 바꾸지 않음"이라 했다 — 어댑터가 날짜형 관측을 받게 계약을 넓힌다. 시각을 지어내지 않는다 |
| `available_at` 의미 | "공개·수신시각" | 수신 기준(매크로·업종), 공개일 다음날 00:00과 수신 중 이른 쪽(재무) + `availability_basis` | 판본마다 근거를 싣는다. 과거 fx_daily의 규칙값(다음날 06:00)은 이 정의가 아니다 |
| 재무 `published_at`(#995 `financial_observations`) | 순간값 필수 | 접수일(DATE)만 | 같은 이유로 날짜형을 받거나 `available_at`만 쓴다 |
| Q4 EPS | 해당 분기 EPS | `FY_MINUS_9M` 유도(근사) | 유도 행을 쓸지 소비 규칙에서 결정(결정 ②로 유도는 적재) |
| 구성종목 없는 시점 | 불완전 비중 거절 | 0행 | 일치 |
