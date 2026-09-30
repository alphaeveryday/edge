실데이터 분석은 기존 툴 이름·반환 본문·최종 화면 JSON을 유지하고, 원천 공급과 실행 출처만 분리한다. 이 문서는 구현 단위와 실제 지원 범위를 기록한다.

## 실행 출처

- 기존 fixture 인수는 synthetic. 실제 원천 툴은 source_tools로 전달하며 두 입력을 동시에 받지 않는다.
- movement_analyses / outlook_analyses에 data_source를 저장. synthetic / database만 새 실행에 사용하고, 과거 미분류 기록은 unknown으로 보존한다.
- 이전 분석 자동 선택·명시적 이전 ID·같은 실행 ID 재사용·과거 항목과 근거 재사용에서 출처 일치를 검사한다.
- 과거 unknown 발행본은 조회 가능하지만 새 분석의 이전 글·근거로 사용하지 않는다. 종목 코드만으로 기존 데이터를 실제로 분류하지 않는다.
- 이 컬럼은 서버 저장 문맥이다. 에이전트가 선택하거나 화면 JSON을 변경하는 필드가 아니다.
- Flyway V202609301400 적용 후 새 실행 코드를 배포한다. 기존 코드와 읽기는 컬럼 추가 후에도 유지된다.

## 연결 순서

1. 출처 격리 → 전체 구성·뉴스 → 수급 → 가격·차트.
2. 각 원천의 시점·필요 컬럼·기간·결측을 먼저 확인하고 계산 및 실제 호출을 검증한다.
3. 매크로·재무는 9월 30일 조회에서 0행. 미확보를 목자료로 채우지 않는다.

## 구성종목·뉴스 조회 계약

| 툴 | 실제 원천 | 반환·제한 |
|---|---|---|
| get_etf_holdings | etf_holding_snapshot + status + instrument/entity | 현재 시각까지 적재된 최신 구성. 원래 비중 유지. 누락 시 coverage=partial, observed_weight_ratio 표시. 가중 계산에는 불완전 구성을 사용하지 않음 |
| search_news_threads | document → assertion → event_evidence → source_event → event_thread_link | DB 사건 ID·단계·스레드로 묶음. 같은 사건의 추가 기사만 중복 수로 표시. 연결 없는 기사도 unthreaded_news로 제공 |
| get_issue_evidence | document + news_document | true는 확보된 발췌와 body_kind=excerpt, false는 동일 기사 ID·제목. 발췌 확보 시점이 늦으면 body=null |

- 읽기 전용·repeatable read로 원천을 한 번 읽고 DB 연결을 닫은 후 에이전트 실행.
- 조회는 DB instrument_id, 에이전트의 instrument_id와 화면 종목 코드는 KRX ticker로 통일. 종목 마스터의 일대일 매핑을 사용.
- ETF와 상위 5개 편입종목·발행기업에 연결된 최근 30일 기사 최대 300개. 초기 제목 100개, 탐색 사건 100개. 한도 도달을 표시.
- 스레드 관계는 현존하는 연결 중 분석 시각 이전에 확인된 것만 사용. 삭제·정정된 과거 관계까지 복원한 과거 재생은 보장하지 않음.
- 원천이 비어 있는 다른 요인을 목자료로 채우지 않음. 반환 JSON과 저장할 툴 결과는 동일.

## 수급 조회·계산 계약

| 툴 | 데이터 | 계산 |
|---|---|---|
| calculate_investor_flow / sum_investor_net_flow | 개별 편입종목의 investor_flow_daily, 이전 확정 거래일부터 최대 30일 | 합계·부호 일수·최신일부터 연속. 종목 자체의 수급이며 ETF 거래가 아님 |
| calculate_weighted_flow / sum_weighted_net_flow | 같은 기간의 전체 구성·일별 비중·전 종목 확정 수급 | 날짜별 비중 가중 → 합계/빈도/연속. 0 비중은 기여 0. 누락 또는 불완전 구성일은 계산 거부 |

- 외국인=net_val_foreign, 기관=net_val_institution_total, 개인=net_val_individual. 원 단위 정수. NULL을 0으로 대체하지 않음.
- 기준일은 분석일 직전 KRX 거래일. 당일 수급은 제외. 조회 누락 때문에 기준일을 더 과거로 옮기지 않음.
- 달력은 현재 파이프라인에 등록된 2026년 KRX 휴장일과 동일. 지원 연도 밖은 거부. 반환 이력으로 휴장일을 추측하지 않음.
- 현재 091160 구성 적재는 22건 중 21건이므로 전체 가중 수급은 미제공. 개별종목 계산은 이 제한과 독립.

