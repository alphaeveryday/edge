# 종목별 요인 조회

`get_instrument_factors`는 지정한 주식 또는 ETF의 차트·수급·밸류·매크로를 한 번에 조회한다. 에이전트는 전체를 읽거나 필요한 요인만 선택한다. 화면 카드는 같은 결과를 서버가 조립하며, 에이전트는 중요한 자료를 선택해 추가 탐색·계산을 이어간다.

## 호출 계약

**Signature:** `get_instrument_factors(instrument_id, factors?)`

| Parameter | Type | Required | Meaning |
|---|---|---|---|
| `instrument_id` | string | Yes | 현재 분석에서 조회 가능한 주식·ETF의 실제 ID. 다른 대상을 현재 ETF로 조용히 대체하지 않음 |
| `factors` | string[] | No | `chart`, `flow`, `valuation`, `macro`. 생략하면 네 요인 전체, 지정하면 해당 요인만 반환 |

- `null`, 빈 목록, 중복·미지원 요인, 알 수 없는 종목은 거부한다.
- 분석시각은 실행 문맥에 고정한다. 모델이 호출 인수로 바꾸지 못하며 이후 공개·관측 자료를 사용하지 않는다.
- 미요청 요인 키는 생략한다. 요청했지만 전혀 제공할 수 없는 요인은 `null`, `result.unavailable`에 요인별 사유를 적는다.
- 일부 값만 부족하면 그 값만 `null`로 남기고 가능한 값은 반환한다. 결측을 0·중립으로 대체하지 않는다.
- 각 요인의 필수 자료를 따로 검사한다. 차트 장기 이력 부족이나 밸류 결측이 다른 요인의 결과를 막지 않는다.

```json
{"instrument_id":"stock-001","factors":["flow","valuation"]}
```

## 반환·저장

| Field | Meaning |
|---|---|
| `tool_run_id` | 성공 실행 ID. 반환과 저장이 같으며 최종 설명의 수치 근거에 연결 가능 |
| `result.instrument_id` | 요청한 종목 ID |
| `result.instrument_name` | 원천에 있는 종목명 |
| `result.analysis_at` | 실행 문맥의 자료 상한 시각 |
| `result.chart`, `flow`, `valuation`, `macro` | 요청한 요인의 객체 또는 전체 미확보 시 null |
| `result.unavailable` | 전체 미확보 요인명→사유. 없는 데이터와 요청하지 않은 요인 구분 |

이력은 `columns`와 `rows`, 나머지는 객체다. 반환 객체 전체를 그대로 저장한다. 조회가 반환한 관측·계산값은 해당 실행 ID로 추적하며, 이력에서 새 합계·빈도·전환을 주장하려면 해당 계산 툴을 호출한다. 조회 성공이 중요도·인과·미래 수익의 타당성을 인증하지는 않는다.

## 요인별 범위

| 요인 | 반환할 자료 | 시점·범위 |
|---|---|---|
| 차트 | 가격, 기존 6개 지표, 모멘텀·바닥지수, 지수 이력 | 요청 종목의 현재 가용 가격과 지표. 지수 이력 최대 5관측 |
| 수급 | 확정일·단위·범위, 투자자별 순매수 이력, 기존 카드용 합계·연속·좌수 변화 | 최신 확정일부터 최대 30거래일. 주식은 자체 수급, ETF는 구성종목의 일별 비중 가중 |
| 밸류 | 주식의 가격·공개 재무·PER·PBR 또는 ETF 가중 PER·PBR과 기존 분배율 | 당시 공개된 재무·가격. ETF 가중 PER·PBR은 비율마다 그 값이 있는 종목의 비중 합이 70% 이상일 때만 내고(`ratio_coverage`), 그 종목들의 비중으로 나눈 평균임. 70% 미만이면 null |
| 매크로 | 계열명·단위·관측 이력과 기존 화면 계산값 | 계열별 최근 최대 21관측. 거래일·달력일·월별 관측을 혼동하지 않음 |

### 차트

- `price_krw`, `observed_at`: 요청 종목의 실제 가격과 관측시각.
- `finalized_through`, `finalized_observed_at`: 완료 거래일과 해당 자료의 공개시각.
- `ma60_direction`: `rising`, `falling`, `flat` 또는 null.
- 기존 지표: `ma20_distance_pct`, `ma60_direction`, `new_closing_high_count_20d`, `distance_from_52w_closing_high_pct`, `turnover_ratio_previous_day`, `atr14_pct`.
- `momentum_index`, `bottom_index`, `indicator_history`: 이력은 `columns: ["at", "momentum_index", "bottom_index"]`, 최대 5관측의 시간순 `rows`.
- 현재가를 사용하는 이격과 완료 거래일 기반 횟수·거래대금·ATR의 시점을 구분한다. 데이터가 부족한 지표만 null이다.
- `volume_comparison`: 같은 시각 비교 원천이 없으면 null. 전일 거래대금 비율로 대체하지 않는다.

### 수급

- `scope`: 주식 `instrument`, ETF `holdings_weighted`. ETF 자체 투자자 매매로 표현하지 않는다.
- `finalized_through`, `unit: "KRW"`, `history`: 열은 `date`, `foreign`, `institution`, `individual`.
- 가용 확정 이력을 최대 30거래일 반환한다. 빠진 날을 0으로 채우지 않는다. ETF는 각 거래일의 구성(관측 비중 70% 이상)과 비중·수급이 필요하다. 그날 구성이 비중 검증을 통과하지 못하면 구성이 없는 날처럼 그날에서 이력을 멈추고 실행 로그(`Weighted flow history stopped`)에 남긴다.
- 20일 합계·연속·좌수 변화는 화면 유지에 필요한 기존 계산 결과다. 필요한 기간이 없으면 값만 null이며 연속 경계 미확인을 정확한 일수로 표시하지 않는다.

| ETF 수급 추가 필드 | 의미 |
|---|---|
| `weighted_foreign_net_amount_20d`, `weighted_institution_net_amount_20d` | 최근 20확정 거래일 가중 순매수 합계, 원 |
| `weighted_foreign_net_buy_streak`, `weighted_institution_net_buy_streak` | 최신 확정일까지 연속 순매수 일수. 중단 경계 미확인이면 null |
| `etf_units_change_20d_pct`, `units_observed_at` | 20거래일 전 대비 좌수 변화율(%)과 공개시각. 21개 일별 좌수 필요 |
| `observed_at` | 반환한 수급 원천의 가장 늦은 공개시각 |

### 밸류

- 주식: `price_krw`, `price_observed_at`, `financials_published_at`, `ttm_period_end`, `ttm_eps_krw`, `bps_krw`, `per`, `pbr`.
- ETF: `scope: "holdings_weighted"`, `holdings_as_of`, `weighted_per`, `weighted_pbr`, 기존 `distribution_yield_12m_pct`.
- `observed_at`: 계산에 사용한 가격·재무의 가장 늦은 시각. 분배율은 `distribution_observed_at` 별도 표시.
- PER과 PBR은 개별 가용성을 판단한다. PER 불가가 정상 PBR까지 막지 않는다.

### 매크로

- `series`: 각 원소의 `series`, `unit`, `columns`, `rows`. 관측 열은 `at`, `value`, `available_at`. CPI에는 `reference_period` 추가.
- 기존 화면용 환율·금리·원자재 변화·정책 일정 값은 같은 객체에서 제공한다. 일정이 없으면 null이며 새 이벤트를 추정하지 않는다.
- `metric_observed_at`, `metric_subjects`: 화면용 수치별 관측시각·대상. `next_policy_decision`: 확보한 다음 결정의 `decision_at`, `available_at`, `subject` 또는 null.
- 공통 거시 관측을 반환하는 것이며 개별 종목에 미친 영향은 에이전트의 해석이다.

## 화면 조립과 감사

- 서버는 전망 실행 전에 ETF 전체 요인을 한 번 조회하고 그 결과로 화면 수치 조립.
- 이후 개별 종목·부분 조회는 이 ETF 화면 수치를 덮어쓰지 않음.
- 저장 시 종목·요인·값·관측시각·실행 ID를 툴 결과와 대조.
- 기존 DB 테이블·앱 응답 유지. 과거 발행물의 이전 툴 근거도 조회 가능.
- 가격·수급 이력은 최신 거래일부터 누락 직전까지의 연속 구간만 사용. 부족한 지표는 null.
- 중복 관측·잘못된 단위·불완전한 구성비중은 오류로 거부.
- 이력이 없어도 별도로 확보된 금리 일정·ETF 좌수·분배율은 유지.

## 반환 예시

가상 자료의 개별 종목 밸류만 조회한 반환 구조. 실행 ID만 설명용 값.

```json
{"instrument_id":"000660","factors":["valuation"]}
```

<details>
<summary>반환 JSON 펼치기</summary>

```json
{
  "tool_run_id": "example-run",
  "result": {
    "instrument_id": "000660",
    "instrument_name": "SK하이닉스",
    "analysis_at": "2026-09-21T10:00:00+09:00",
    "valuation": {
      "scope": "instrument",
      "price_krw": 64300,
      "price_observed_at": "2026-09-18T18:00:00+09:00",
      "financials_published_at": "2026-08-24T18:00:00+09:00",
      "ttm_period_end": "2026-03-31",
      "ttm_eps_krw": 6000,
      "bps_krw": 50000,
      "per": 10.716666666666667,
      "pbr": 1.286,
      "observed_at": "2026-09-18T18:00:00+09:00"
    }
  }
}
```

</details>

## 검증할 경계

- 같은 종목·시점에서 전체 호출과 부분 호출의 해당 요인 결과가 같다.
- 주식 호출이 다른 종목이나 ETF 가격·가중값을 반환하지 않는다.
- 30·21·5는 최대 반환 개수다. 예시의 짧은 이력 개수나 부족한 날짜를 고정 조건으로 오해하지 않는다.
- 한 요인·지표 결측에서도 무관한 결과는 반환된다. 숨겨진 미래 자료·부분 가중 재정규화·동시각 거래량 대체는 허용하지 않는다.
- 서버 화면 수치와 이 실행 결과가 연결되고, 과거 실행 기록과 감사 자료는 그대로 읽을 수 있다.
