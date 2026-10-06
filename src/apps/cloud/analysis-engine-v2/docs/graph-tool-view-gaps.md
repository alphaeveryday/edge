# 뷰에 붙지 못한 도구 입력

그래프 도구로 옮기는 동안 `ontology_view` 뷰로 읽을 수 없는 입력을 여기에 모은다. 작업을 멈추는 사유가 아니며, 전체 도구 이전이 끝난 뒤 한 번에 보고한다.

각 항목은 해당 도구 차례에 확인한 사실로 갱신한다. "확인 전"은 코드에서 원본 테이블 사용만 본 상태다.

| 원본 | 쓰는 도구 | 뷰에 없는 이유 | 상태 | 현재 처리 |
|---|---|---|---|---|
| `minute_price_trigger` | `calculate_chart_indicators`, `evaluate_indicator_transition`, `get_instrument_factors` | 대응하는 뷰가 없다 | 확인 전 | 원본 조회 유지 |
| `macro_observations_as_of()` | `get_macro_observations`, `compare_macro_observations`, `get_instrument_factors` | 분석 시각 기준 판본을 고르는 함수다. `macro_observation` 뷰가 같은 판본을 돌려주는지 대조하지 않았다 | 확인 전 | 원본 조회 유지 |
| `financial_quarters_as_of()` | `calculate_valuation`, `calculate_weighted_valuation`, `get_instrument_factors` | 위와 같다. 대조 대상은 `financial_metric` 뷰다 | 확인 전 | 원본 조회 유지 |
| `etf_holding_snapshot_status` | `get_etf_holdings`, 가중 수급·밸류 계산 | `etf_holding` 뷰에 수집 건수(`input_count`·`valid_count`)가 없다. 엔진 도구는 이 값으로 `coverage=full/partial`을 정해 가중 계산을 막는데, 그래프 도구는 `source_snapshot_completeness: not_certified`만 말할 수 있다 | 확인함 (2026-10-06) | 그래프 `get_etf_holdings`는 추가했지만 엔진의 기존 도구는 대체하지 않고 둔다 |

## 뷰는 있지만 자료가 비어 있는 것

도구 문제가 아니라 뷰가 읽는 자료의 범위다. 같은 보고에 포함한다.

| 뷰 | 관측 | 영향 |
|---|---|---|
| `equity` | 2,766건 전부 보통주다. 우선주가 한 건도 없다 | 우선주 여부를 묻는 질문은 `ExchangeSecurityClassification`에만 남은 이름으로 추정해야 한다 |
| `etf` | 40건이다. `TIGER K방산&우주`가 없고 운용사 이름은 39건이 비어 있다 | 두 ETF 비교(CQ01)는 자료가 없어 불가 |
