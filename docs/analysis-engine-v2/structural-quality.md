원하는 글에서 필요한 질문을 도출하고, 질문에 맞는 자료·계산 툴을 제공한 뒤 실제 모델과 PostgreSQL 저장 결과를 함께 검수한다. 목자료 시험이며 투자 성과 검증이나 실제 원천 연결 완료를 뜻하지 않는다.

## 선례와 적용

| 선례 | 가져오는 방법 | 적용 한계 |
|---|---|---|
| [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf), 가정 점검·경쟁 설명 | 수율 상승을 공정 개선과 제품 구성 변화로 구별할 자료를 찾고, 중요한 반대 증거로 판단을 수정 | 경쟁 설명 수를 채우거나 근거 개수로 우열을 채점하지 않음. 금융 예측 성능의 실증은 아님 |
| [FinQA](https://aclanthology.org/2021.emnlp-main.300/)·[TAT-QA](https://aclanthology.org/2021.acl-long.254/) | 표·원문에서 피연산자를 선택하고 계산은 실행 가능한 함수로 분리 | 숫자 정답이 좋은 투자 판단이나 인과 설명을 보증하지 않음 |
| [Arcade PATs](https://arcade.dev/patterns/llm.txt) | 질문 단위 툴, 명확한 인수·후속 조회 안내·코드의 범위 제한 | 화면용 묶음을 그대로 에이전트 툴로 노출하지 않음. 모든 패턴을 의무 도입하지 않음 |
| [Anthropic 도구 설계](https://www.anthropic.com/engineering/writing-tools-for-agents) | 실제 에이전트로 인터페이스를 비교하고 실패 호출·누락 자료·비용을 함께 확인 | 짧은 설명이나 작은 호출 수 자체를 최적화하지 않음 |
| [CheckList](https://aclanthology.org/2020.acl-main.442/) | 기본 기능·불변성·방향 변화 시험을 분리 | 분류기의 정확도를 전망 글에 그대로 옮기지 않음. 의미 검수는 별도 |
| [Anthropic 에이전트 평가](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 반복 실행, 호출 기록과 최종 DB 상태 구별, 보지 않은 사례 검수 | 고정 호출 순서를 정답으로 삼지 않음. 한 번 성공을 안정성으로 보고하지 않음 |

## JTB와 가까운 가능세계

[JTB와 안전성 논의](https://plato.stanford.edu/entries/knowledge-analysis/)를 다음 개발 검증으로 번역한다. 철학적 지식 정의를 증명하는 것은 아니다.

- B: 검증할 주장을 명시. 예: ‘후속 자료가 개선의 지속성 판단에 반영된다.’ 모델 내부 믿음을 측정한다고 주장하지 않음.
- T: 관측 사실·수치·저장 동작을 독립 자료 및 계산과 대조. 미래 전망은 현재 참이라고 인증하지 않음.
- J: 그 판단을 뒷받침하는 실제 원문 조회·계산 결과·검수 이유 연결.
- 안전성: 정답과 숫자가 우연히 맞아도 다른 종목·기간 근거면 실패. 관련 없는 순서 변화에는 판단 유지, 중요한 사실 변화에는 판단 수정.
- 범위: 순서, 중복, 미래 공개, 일회성 개선, 예상 반영, 핵심 자료 누락, 반대 매크로, 다른 업종 사례. 이 유한 시험 집합 밖의 강건성은 미검증.

## 설명 목표와 논리

1. 사건과 ETF 노출 확인 → 무엇이 달라졌는가.
2. 원문·후속 자료 조회 → 왜 달라졌고 지속 가능한가.
3. 같은 대상·기간의 실제·예상 비교 → 기존 기대와 얼마나 다른가.
4. 출처가 있는 EPS와 명시한 배수로 계산 → 현재 가격에서 어느 범위가 가능한가.
5. 매크로·차트·수급·다른 구성종목 확인 → 반론이 결론을 얼마나 바꾸는가.
6. 결론 먼저, 짧은 완결 불릿 → 각 근거가 판단에 주는 의미를 읽는 순서로 설명.

모든 사례에 목표가·과소평가 결론을 강제하지 않는다. 자료가 없으면 해당 주장을 만들지 않는다. 기사에 적힌 생산 개선을 시장 전체의 미인지로 바꾸지 않는다.

## 툴 계약 — 구현 전 명세

기존 뉴스·보유종목·수급·매크로·지수 계산을 재사용한다. 새 함수는 밸류 자료 탐색·계산과 차트 탐색에 한정한다. 공통 자료 조회는 [종목별 요인 조회](../../src/apps/cloud/analysis-engine-v2/docs/instrument-factors.md) 하나로 통일한다. 서버는 동일 ETF 조회 결과로 상세 카드를 조립한다.

| 함수 | 인수 | 반환 본문 | 최종 근거 |
|---|---|---|---|
| `get_financial_observations` | `instrument_id` | `columns + rows`: 관측 ID, 지표, 값, 단위, 대상 기간, 실제/예상, 작성자, 공개시각, 근거 기사 ID | 아니오 |
| `compare_financial_observations` | `previous_id`, `current_id` | 두 원천 행, 차이, 상대변화. 같은 종목·지표·단위·대상 기간만 비교 | 예 |
| `calculate_valuation_range` | `eps_id`, `per_low`, `per_high` | EPS 원천·배수 가정·현재가·가격 범위·현재가 대비 변화 | 예. 배수의 타당성은 내용 검수 |
| `get_instrument_factors` | `instrument_id`, 선택 `factors` | 요청 종목의 차트·수급·밸류·매크로 자료와 시점. 전체 또는 일부 요인 | 예. 반환값의 새로운 계산은 전용 툴 사용 |
| `sum_investor_net_flow` | `instrument_id`, `investor`, `lookback_days` | 확정일의 부호 있는 순매수 합계·기간 | 예 |
| `sum_weighted_net_flow` | `investor`, `lookback_days` | 전 구성종목을 일별 비중으로 반영한 순매수 합계·기간 | 예 |

첫 실제 실행에서 합계와 방향 선택의 충돌이 5회 발생했다. 합계는 방향 인수가 없는 함수로 분리하고 기존 함수는 에이전트에게 빈도·연속만 노출한다. 기존 함수의 서버 구현·감사 정의는 과거 실행 재현을 위해 보존한다.

- 관측은 고정 분석시각까지 공개·입수된 것만. 중복 ID·모호한 종목·기간·단위·역순 공개 비교 거절.
- 실제와 예상 비교는 `kind`를 보존. 예상끼리의 차이를 실적 증가로 표현하지 않음.
- 범위 계산은 양수 EPS, `0 < per_low <= per_high`, 양수 현재가. `price = EPS × PER`, `return_pct = 100 × (price / current_price − 1)`.
- 범위 툴 v2는 `current_per = current_price / EPS`도 반환. `return_low_pct`가 양수면 낮은 배수 시나리오도 현재가보다 높다는 뜻이며 최대 손실·가격 지지선을 뜻하지 않음. 과거 밴드 아래라는 사실만으로 시장 미반영을 확정하지 않음.
- 비교는 `difference = current − previous`, 양수 이전값에서 `percent_change = 100 × difference / previous`. 이전값이 0·음수면 상대변화는 null.
- 출처·수식은 정의에, 인수·반환값은 기존 `tool_runs`에 보존. 반환과 저장은 동일 객체.
- 조회 본문을 초기 입력에 중복해서 넣지 않음. 초기에는 공개된 관측의 종목·지표·기간 목록으로 탐색 가능 범위 안내.

### 인수 형식

| 인수 | 형식 | 의미 |
|---|---|---|
| `instrument_id` | string | 초기 구성종목에 있는 식별자 |
| `previous_id`, `current_id` | string | 조회 결과의 관측 ID. 이전·현재는 공개시각 순 |
| `eps_id` | string | KRW 기준 연간 EPS 관측 ID. 분기 EPS를 연간으로 간주하지 않음 |
| `per_low`, `per_high` | number | 양수 배수. 하단 ≤ 상단. 타당성은 에이전트 판단·내용 검수 |
| `metrics` | string[] | 차트의 요청 지표 이름. 중복 불가 |
| `investor` | string | `foreign`, `institution`, `individual` |
| `lookback_days` | integer | 최신 확정일부터 1~30거래일 |

<details><summary>실제·예상 조회 예시</summary>

```json
{"instrument_id":"000660"}
```

```json
{"tool_run_id":"example-read","result":{"columns":["observation_id","instrument_id","metric","value","unit","period","kind","author","published_at","available_at","news_id"],"rows":[["eps-new","000660","eps",7200,"KRW_per_share","2027","estimate","가상 증권사 3곳 평균","2026-09-17T07:30:00+09:00","2026-09-17T07:30:00+09:00","expectations"]]}}
```

</details>

<details><summary>예상 수정·가격 범위 호출 예시</summary>

```json
{"previous_id":"eps-old","current_id":"eps-new"}
```

비교 반환의 `previous`, `current`는 선택한 원천 객체 전체이며 `difference=700`, `percent_change=10.76923076923077`이다. 계산값은 표시 반올림 전 값으로 저장한다.

```json
{"eps_id":"eps-new","per_low":9,"per_high":11}
```

범위 반환은 `eps_observation`(선택한 원천 객체), `per_assumptions`(`low`, `high`), `current_price`, `price_date`, `price_low=64800`, `price_high=79200`, `return_low_pct`, `return_high_pct`다. 모든 반환은 `{tool_run_id, result}`로 감싸 동일하게 저장한다.

</details>

<details><summary>차트·수급 호출 예시</summary>

```json
{"metrics":["ma20_distance_pct","turnover_ratio_previous_day"]}
```

차트 반환은 `instrument_id`, `price`, `price_at`, `metrics`다. 각 지표는 `key`, `value`, `observed_at`으로 구성한다. `price_at`은 장중 실제 관측시각 또는 확정 종가의 거래일이다.

```json
{"investor":"foreign","lookback_days":5}
```

가중 합계 반환은 기존 수급 계산의 `amount_krw`, 대상·시작일·종료일을 그대로 사용한다. 개별 합계 호출에는 `instrument_id`를 추가한다.

</details>

## 시험과 감사

- 계산: 손계산, 미래 공개 차단, 같은 수치의 다른 종목·기간 거절, 입력 순서 불변, 반환과 저장 일치.
- 행동: 생산 개선 원인을 본문에서 확인, 최신 예상 확인, 반대 자료 반영. 다른 타당한 조회 경로 허용.
- 문장: 고객 취향·메타 규칙으로 검수. 정규식이나 런타임 LLM judge로 의미 합격을 만들지 않음.
- 대시보드: 원하는 결과·변경한 사실·실제 글·호출·감사·검수 상태. 미실행·실행 실패·미검수·내용 실패를 구별.
- 품질 통과는 실제 결과에 사람이 검수 이유를 기록한 경우만. 파일 존재나 JSON 성공만으로 합격 처리하지 않음.

## 실행 순서

1. 자료·좋은 설명·반례를 함께 작성하고 모델에게 정답은 전달하지 않음.
2. 논리별 필요 자료와 툴 계약 확인.
3. 실패 테스트 → 최소 구현 → 계산·감사 검사.
4. 기존 카드 방식과 선택 계산 방식으로 실제 모델 비교. 입력·모델·사례 동일.
5. 검증된 도구와 분석·편집 프롬프트 적용, 대표 및 보지 않은 사례 재검수.
6. PostgreSQL 저장·화면 조립·갱신을 확인하고 최종 감사 대시보드 제공.

추가 원천·벤더·Flyway 변경은 현재 범위에 없음. 클라우드 인증이 불가하면 로컬 DB 검증과 구별하여 보고한다.
