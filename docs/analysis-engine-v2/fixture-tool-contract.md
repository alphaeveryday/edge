# 목데이터 도구 계약

실제 계산 도구가 목데이터를 조회하고 근거 ID를 반환한다. 도구 결과는 감사 저장 후 그대로 에이전트에게 전달한다. 원천 DB 연결은 별도 작업이다.

## 공통

- `FixtureTools(fixture).schemas`: 함수명·설명·JSON 인수 명세.
- `.definitions`: 함수 버전·관리자 설명·LaTeX 수식·출처 이름.
- `.call(name, arguments)`: `{tool_run_id, result}`. 저장은 호출자를 감싸는 `AuditedExecution`이 담당.
- `.initial_input()`: 시점 제한된 원자료. 시계열은 `columns/rows`, 뉴스는 객체 목록.
- `context`: `etf_code`, 명시적 시차가 있는 `analysis_at`, 확정 수급일 `flow_as_of_date`.
- 모든 자료는 `available_at <= analysis_at`. 미래 관측·미래 발표 제외. 결측은 0으로 채우지 않는다.

## 뉴스·구성종목

| 도구 | 인수 | 결과 | 최종 근거 |
|---|---|---|---|
| `search_news_threads` | 없음 | 스레드→단계→고유 사건별 대표 기사와 중복 수. 최대 100개 사건 | 아니오 |
| `get_issue_evidence` | `news_ids: string[]`, `include_body: boolean` | 기사 ID·제목. true는 확보 본문 추가 | false일 때 |
| `get_etf_holdings` | 없음 | 최신 공개 편입일과 구성종목 비중 | 예 |

- 뉴스 행: `news_id,title,body,published_at,available_at,thread_id,stage,event_id`. 동일 사건 ID만 중복으로 묶는다. 제목 유사도로 병합하지 않는다.
- 대표 기사는 분석시각에 공개된 같은 사건의 최신 기사. 각 중복 보도 수는 대표 기사 제외 건수.
- 편입 행: `instrument_id,weight,as_of_date,available_at`. 목데이터 초기 범위는 주식만, 전체 종목 확보, 비중 합 1. 불완전 비중은 거절한다.
- 기사 본문은 자료 내용이며 명령이 아니다. 에이전트가 읽고 영향·중요도를 판단한다.

## 수급

| 도구 | 필수 인수 | 결과 |
|---|---|---|
| `calculate_investor_flow` | `instrument_id, investor, lookback_days, operation, direction` | 개별종목 금액 합계·빈도·최신일부터 연속 |
| `calculate_weighted_flow` | `investor, lookback_days, operation, direction` | 구성종목 일별 가중 금액을 집계한 합계·빈도·연속 |

- investor: foreign/institution/individual. operation: sum/frequency/streak. direction: net_buy/net_sell, sum일 때만 none. 기간은 1~30거래일.
- 원자료 `flow`: `instrument_id,date,investor,net_amount_krw,available_at,finalized`. 달력 `trading_dates`와 `flow_as_of_date`로 정확한 구간을 선택.
- 초기 구현 선택: 각 거래일 당시 공개된 최근 비중으로 일별 가중합 후 빈도·연속 계산. 비중 합 1, 종목·거래일 하나라도 누락이면 계산 불가. 과거 흐름에 현재 비중을 소급하지 않음.
- 합계 $S=\sum_d F_d$, 가중 $F_d=\sum_i w_{i,d}x_{i,d}$, 빈도 $M=\sum_d[\operatorname{sign}F_d=s]$.
- 연속은 최신 확정일부터 역순. 0·반대 부호에서 중단. 조회 구간 전부 같은 부호이면 `exact=false`와 `streak_days` 하한을 반환. 결측은 중단 신호가 아님.
- 가중 금액은 Decimal로 계산하고 결과 표시 시에만 JSON 숫자로 변환. 방향 판정 전 반올림하지 않음.
- 두 도구 모두 최종 근거 가능. ETF 자체 순매수라고 표현하지 않음.
