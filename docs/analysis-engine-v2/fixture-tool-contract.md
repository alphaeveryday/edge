# 목데이터 도구 계약

실제 계산 도구가 목데이터를 조회하고 근거 ID를 반환한다. 도구 결과는 감사 저장 후 그대로 에이전트에게 전달한다. 원천 DB 연결은 `source_inputs.py`(ALPHA-1130 — 매크로·재무를 `*_as_of` 함수에서 이 fixture 행 형태로 읽는 어댑터, 통합 테스트 `integration_tests/test_source_inputs_postgres.py`)까지 있고, 실행 경로에 붙이는 것은 별도 작업이다.

## 공통

- `FixtureTools(fixture).schemas`: 함수명·설명·JSON 인수 명세.
- `.definitions`: 함수 버전·관리자 설명·LaTeX 수식·출처 이름.
- `.call(name, arguments)`: `{tool_run_id, result}`. 저장은 호출자를 감싸는 `AuditedExecution`이 담당.
- `.initial_input()`: 시점 제한된 원자료. 시계열은 `columns/rows`, 뉴스는 객체 목록.
- 여러 종목의 시계열은 종목 ID를 키로 한 객체에 각각의 표를 넣는다. 공통 대상·단위를 행마다 반복하지 않는다.
- 수급: `flow[종목ID]`는 `unit=KRW`, `metric=net_amount`, `columns=[date,foreign,institution,individual]`. 없는 관측은 null, 실제 0은 0. 같은 종목·날짜·투자자의 중복은 거절.
- 일봉: `prices[종목ID]`는 `date,high,low,close,volume,turnover`. 가격 스냅샷은 ETF 대상과 `at,price,high,low,available_at` 표. 가격을 지수 `value`로 바꾸지 않는다.
- 매크로 초기 입력과 조회는 동일한 `at,value,available_at` 표. CPI는 `reference_period` 열 추가(원자료 미제공이면 null). `at`은 원자료 관측시각, `available_at`은 공개·수신시각이며 서로 대체하지 않는다.
- `context`: `etf_code`, 명시적 시차가 있는 `analysis_at`, 확정 수급일 `flow_as_of_date`.
- 모든 자료는 `available_at <= analysis_at`. 미래 관측·미래 발표 제외. 결측은 0으로 채우지 않는다.
- 거래 날짜는 KST. UTC로 전달된 분석시각도 같은 한국 거래일로 해석한다.

### 에이전트에게 보여주는 툴 명세

- 투자자·방향·연산처럼 고정된 선택지만 enum으로 표시한다.
- 날짜·종목·기사·관측 ID 목록은 초기 입력과 조회 결과에서 읽는다. 같은 목록을 함수 인수 명세에 반복하지 않는다.
- SDK에 표시할 명세만 줄인다. 서버는 원래 인수 명세로 검증한 뒤 도구를 실행하므로 범위 밖 값은 실행·저장하지 않는다.
- `tool_schemas.json`은 에이전트에게 실제 표시한 명세다. 반환·저장 객체와 계산식은 변경하지 않는다.

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

## 차트

- `calculate_chart_indicators()`: ETF RSI14·반전 Williams%R14와 관측시각. 최종 근거 가능.
- `evaluate_indicator_transition(indicator)`: momentum/bottom. 최근 가격 스냅샷 최대5개에서 계산한 지수의 상하단 진입·이탈·유지. 최종 근거 가능.
- 반환은 `indicator, observations, transitions, held_zone`. `observations`는 `columns=[at,value]`와 `rows` 표다. 빈 이력은 빈 rows, 계산 불가 값은 null. 상태 판정은 객체형으로 유지하며 이 반환 형식은 정의 v2로 저장한다.
- 원자료: `prices`의 instrument_id,date,high,low,close,volume,turnover,available_at와 `price_snapshots`의 instrument_id,price,high,low,observed_at,available_at. 단일 `price_snapshot`도 허용.
- 초기 채택: RSI14 Wilder. 15확정 종가의 14변화로 상승·하락 평균 시드, 이후 RMA. 장중값은 전일 확정 RMA에서 오늘 변화만 반영. 평탄 분모는 null.
- 바닥 $100(H_{14}-P)/(H_{14}-L_{14})$. 오늘 고저+이전13일. 0분모 null. 반등확률 아님.
- 상단 ≥80, 하단 ≤20. 유지에는 서로 다른 최근5개 관측 전부 필요.
- 5요인 카드: 현재가 반영 MA20 이격·MA60 방향, 확정20일 신고가(40일 필요), 364일 최고종가 대비, 전일 거래대금/이전20일 평균, Wilder ATR14/전일종가.
- 거래대금은 실제 필드. 현재가×거래량 대체 없음. 52주 데이터 시작일이 부족하면 해당 카드 제외. 모든 필수 거래일 누락은 계산 실패.
- `distance_from_52w_closing_high_pct = 100(P/H-1)`: 양수는 이전52주 최고 종가보다 높음(돌파), 음수는 그보다 낮음, 0은 같음. +1.1%를 '고점보다1.1% 아래'로 읽지 않음. 절댓값·음수 고정·0상한 처리 없음.
- `ma20_distance_pct = 100(P/MA20-1)`: 양수는20일선 위, 음수는 아래, 0은 같음. 장중 두 이격률은 현재 관측T의 가격, 장전은 최신 확정 종가를 사용.
- `new_closing_high_count_20d`는 완료일D까지 최근20확정거래일의 신고가 일수이며 오늘 장중 돌파 횟수가 아님. `turnover_ratio_previous_day`도 완료일D 거래대금 기준.
- 부호·시점 설명을 보강한 `get_factor_metrics`는 정의v2로 저장. 과거v1 감사 정의는 변경하지 않음.

## 매크로

| 도구 | 인수 | 결과 |
|---|---|---|
| `get_macro_observations` | `series` | 공개된 최근21개 관측의 columns/rows. 탐색용 |
| `compare_macro_observations` | `series, previous_at, current_at, operation` | 두 정확한 관측의 difference 또는 percent_change. 최종 근거 |

- 등록 계열: usd_krw(KRW_per_USD), kr_10y_yield/us_10y_yield/kr_cpi_yoy(percent), brent_spot_usd(USD_per_barrel), commodity(지정 원자재 지수).
- 원자료: `macro`의 series,value,unit,observed_at,available_at,subject(선택). 관측 단위는 등록 단위와 일치해야 함.
- `observed_at`은 오프셋 있는 순간값 또는 관측일(`YYYY-MM-DD`). 일별 종가·월별 지표처럼 원천이 시각을 주지 않는 관측(ALPHA-1130 `macro_observations_as_of`)은 관측일 그대로 두고, 도구는 그 한국 날짜가 끝난 뒤(`23:59:59.999999+09:00`)부터 관측된 것으로 센다 — 시각을 지어내지 않고, 반환 `at`·`previous_at`은 원문 문자열이다. `compare_macro_observations`의 `previous_at`/`current_at`도 같은 형태를 받는다.
- 반환의 관측시각 열을 `at`으로 통일한 `get_macro_observations`는 정의 v2. 기존 v1 실행은 그대로 보존한다.
- 차이 $C-P$: 금리·물가는 %p. 상대변화 $100(C/P-1)$: %. 상대변화의 이전값은 양수여야 함.
- 카드: 환율·국고채10년·미국채10년·브렌트 최신값. 원자재20관측 변화에는 `macro_trading_dates.commodity`의 정확한21개 거래일 필요.
- 금리 일정은 `policy_decisions`의 decision_at,available_at,subject. 미래 행사일은 허용하지만 일정의 공개시각은 분석시각 이전. 날짜 차이는 KST.
- 비어 있거나 자료가 부족한 카드는 제외. 금리·환율의 ETF 영향 방향은 계산하지 않음.

## 밸류·요인 카드

| 도구 | 인수 | 결과 |
|---|---|---|
| `calculate_valuation` | `instrument_id` | 개별 PER·PBR, 사용 가격·EPS·BPS·공개시각 |
| `calculate_weighted_valuation` | 없음 | 전 구성종목 비중 가중 PER·PBR과 개별 계산 |
| `get_factor_metrics` | `type`: 차트/매크로/밸류/수급 | `{type, metrics:[{key,value,observed_at,subject?}]}` |

- 세 도구 모두 최종 근거 가능. 요인 상태·스티커는 반환하지 않음.
- `financials`: instrument_id,period(YYYY-Qn),eps,bps,eps_derivation(선택),available_at. EPS는 누적 아닌 해당 분기, KRW 보통주1주 기준. 가격도 같은 주식단위·통화. 초기 입력 표도 `eps_derivation` 열을 싣는다(원자료에 없으면 null).
- 최근 공개된 연속4분기 EPS 합으로 PER, 최신 공개 분기 BPS로 PBR. 동일 분기 여러 공개본이면 분석시각 이전 최신본을 사용.
- 양수 TTM EPS·BPS, 완전한 주식 구성비중 합1만 계산. 미확정 손실·현금·누락 정책을 중립값이나 재정규화로 숨기지 않음.
- `financials` 행의 `eps_derivation`(선택, 원천 DB 어댑터가 싣는다)이 `FY_MINUS_9M`이면 그 분기 EPS는 근사다. `calculate_valuation`은 `derived_periods`·`approximate`, `calculate_weighted_valuation`은 `approximate`·`derived_constituents`·`coverage{constituents,weight}`를 함께 반환한다. 근사 표시를 실을 수 없는 `get_factor_metrics` 밸류 카드는 유도 분기가 포함되면 `weighted_per`를 내지 않는다(`weighted_pbr`만). 공개된 분기에 EPS나 BPS가 `null`이면(우선주 회사의 보통주 BPS 차단, 분모 응답 미확정) 오류 — 앞 분기로 창을 옮기지 않는다. 구성종목 하나라도 계산 불가면 가중 PER·PBR 전체가 없다(부분 커버리지 값 없음).
- $PER_i=P_i/\sum_{q=1}^{4}EPS_{i,q}$, $PBR_i=P_i/BPS_i$, $\bar x=\sum_iw_ix_i$.
- 5년 밴드는 계약 미정으로 제외. ETF 분배율은 12개월 완전 지급 이력이 명시된 경우만 표시. 다른 결측 카드는 0 대신 제외.

## 실행용 시나리오

- `make_fixture(scenario, analysis_at)`: baseline / quiet / unusual_flow / competing_signals / followup. 모든 기사·가격·재무는 가상 자료이며 실제 KODEX 반도체 분석으로 표시하지 않음.
- 달력은 테스트용 평일 달력. 실제 한국 휴장일 연결은 원천 연결 작업.
- 초기 입력: 최근40개 일봉(대상별), 최근30확정일 수급, 전체 최신 편입, 뉴스제목100개, 거시계열별최근21개, 공개재무최근4분기, 최근가격5개, 이전분석. 원자료는 계산 도구와 같은 snapshot에서 읽음.
- 도구는 전체 준비 이력으로 계산. 모델에게 주는 이력 길이가 계산 이력 길이를 제한하지 않음.
- 현재 미구현: 5년 밴드(계약 미정). 값을 생성하거나 0으로 채우지 않음.

## ETF 분배금·발행좌수

- `distributions`: `instrument_id,paid_at,amount_per_unit,available_at`. 실제 지급된 세전 KRW/좌. 현재 ETF 가격과 같은 분할 단위.
- `distribution_history_start`: 완전한 지급 이력을 보장하는 시작 날짜. 분석시점의 1년 전 동일 날짜 이후~현재 지급액 합 / 현재가격 ×100. 윤년2월29일의 직전연도는2월28일. 자료범위 부족이면 카드 제외, 완전이력에서 지급0건은0%.
- `etf_units`: `instrument_id,date,units,available_at`. `trading_dates`의 최근21확정거래일 모두 필요. 좌수는 양의 정수. 변화율 $100(U_D/U_{D-20}-1)$.
- 시각·누락 검사는 다른 자료와 동일. 오늘 장중 좌수를 전일 확정값 대신 쓰지 않음.

## 시간순 재생

- `make_replay_fixture(analysis_at)`:2026-09-14~18 전용. 한 번 만든 원자료를 보존하고 분석시각만 이동. 기존5가지 독립 시나리오를 같은 분석 계보에 섞지 않음.
- 원계약 n1·생산중단 n2는9월14일07시, 추가계약 n3는9월14일12시에 공개. 같은 기사 ID의 내용·공개시각은 이후에도 고정.14일10·11시에는 원계약,12시 이후에는 후속 기사까지 탐색 가능.
-9월15일의 특이 수급은 그날18시부터 사용 가능.9월16일 장전 분석에서 처음 읽음. 이미 공개된 과거 수급·가격·재무를 다시 만들지 않음.
- 장중 가격도 날짜·시각별 고정 스냅샷. 해당 날짜와 분석시각 이전 관측만 선택. 장전은 전일 확정 종가 사용.
- 테스트용 평일 달력이며 실제 거래소 달력·실데이터 재생을 대신하지 않음.
