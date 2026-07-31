---
doc_type: experiment
status: Draft
owner: price-research
created: 2026-07-08
updated: 2026-07-11
related:
  - ../STATE.md
  - ../hypotheses.md
  - ../decisions/RDR-001-factor-selection.md
  - ../../../engineering/specs/price-decomposition-engine.md
---
# EXP-001: 30일 Top-3 피어 회귀

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

> 한 줄 결론: 직전 30거래일 기준 top-3 peer는 대상 종목의 **같은 날 raw log return**을 꽤 설명하지만, 이는 **FF5 잔차 설명이 아니라 raw return 설명**이며, **동시점 attribution이지 tradable prediction이 아니다**. 이 보고서는 **30d lookback 결과만** 다룬다.

## Summary

30d lookback의 top-3 peer raw-return 회귀는 contemporaneous 설명력 기준선으로는 유용하지만, FF5 residual 설명이나 tradable prediction 증거로 읽으면 안 된다. 아래 본문은 그 제한을 전제로 질문, 표본 정의, 결과, 해석을 정리한다.
## Question

- 질문: 직전 **30거래일** raw log return 상관으로 고른 top-3 peer가 대상 종목의 t일 raw log return을 얼마나 설명하는가?
- 이 문서의 범위:
  - **raw log return only** — FF5 residual 버전이 아님
  - **contemporaneous attribution only** — 같은 날 peer 수익률로 설명하는 것이며 예측 전략 검증이 아님
  - **30d lookback only** — 다른 lookback sweep은 이 보고서 범위 밖

## Method

- 입력 산출물은 peer source panel과 `raw_top3_return_*` generated CSV 묶음이다. 상세 경로는 `근거/출처`를 따른다.
- 원천 입력은 기존 FF5 연구 패널과 적격성 gate다.
- `1,119`는 **고정 유니버스 크기**가 아니라 per-stock 보고 CSV에 남은 **최종 보고 종목 수**이고, pooled 평가 표본은 peer source panel의 **60,960 window-ticker rows**다.

## 입출력 테이블 맵
| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| one-off raw-return report | `panel_daily.parquet`, `eligibility.parquet`, `raw_nn_peer_l30.parquet`, `raw_top3_return_summary_l30.csv`, `raw_top3_return_per_stock_l30.csv`, `raw_top3_return_top30_l30.csv`, `raw_top3_return_bottom30_l30.csv`, `raw_top3_return_pooled_l30.csv`, `raw_top3_return_distribution_l30.png`, `07_nearest_peer_regression.md`, `README.md` | 없음 — 직접 mart owner 아님 | `raw_nn_peer_l30.parquet`, generated `raw_top3_return_*` CSV/PNG, `2026-07-08-raw-top3-return-peer-regression.md` | Draft report / `price_peer_set`, `price_peer_set_member`, `price_decomposition_observation` peer-leg 정책 reference only |

### 표본/유니버스 정의

- 기초 유니버스는 고정 종목 리스트가 아니라 각 테스트 월마다 다시 계산하는 **동적 `liquid_core` 적격성 gate**다.
- `liquid_core` 적격 조건은 훈련 구간 통계 기준 `med_price >= $5`, `med_dollar_vol >= $5M`, `nonzero_ret_ratio >= 95%`, `coverage >= 90%`, `n_obs >= 252`다. 구현 경로는 `근거/출처`의 `config.py`, `windows.py`를 따른다.
- 월별 실제 평가 종목 수는 62개 테스트 기간에서 **최소 867, 중앙값 963, 최대 1,126개**다.
- 전체 평가 패널은 **60,960 window-ticker rows**, **62개 테스트 기간**, **1,687개 고유 ticker**로 구성된다. 즉 `1,119`는 월별 `liquid_core` 편입 종목 수와도, 원시 union ticker 수와도 다르다.
- 최종 per-stock CSV/분포는 **`n_windows >= 20`**인 ticker만 유지하므로 **1,119개**가 남고, **568개**는 점수화된 window가 20개 미만이라 제외된다.
- 이런 설계는 월별 동적 gate로 **survivorship bias**와 **상장폐지 말단 구간 오염**을 줄이고, `n_windows >= 20` 컷으로 **짧은 이력 종목의 불안정한 per-stock R² 분포**를 줄이기 위한 것이다.
- 계산은 기존 패널을 사용한 **one-off raw-return 분석**이다. 재사용 가능한 파이프라인 스크립트로는 아직 승격하지 않았다.

| 필드/지표 | 의미 |
|---|---|
| `in_r2` | 각 ticker의 in-window 설명력 |
| `oos_r2_free3` | top-3 peer 자유계수 OOS 설명력 |
| `oos_r2_ew3` | top-3 peer 등가중 OOS 설명력 |
| `oos_r2_rand3` | 무작위 3종목 placebo OOS 설명력 |
| `avg_corr` | 선택된 top-3 peer와의 30거래일 평균 상관 |
| `same_sub` | top-3 peer 중 같은 sub-industry 비중 |
| `same_sec` | top-3 peer 중 같은 sector 비중 |

<details><summary>예시 JSON: per-stock row (`AAPL`)</summary>

```json
{
  "ticker": "AAPL",
  "n_windows": 62,
  "n_days": 1299,
  "in_r2": 0.8110328899185788,
  "avg_corr": 0.7769518620212289,
  "same_sub": 0.001697792869269949,
  "same_sec": 0.05425552810582706,
  "oos_r2_free3": 0.6580968337926819,
  "oos_r2_ew3": 0.5747934122730215,
  "oos_r2_rand3": 0.1225579223860781
}
```

</details>

## Data/artifacts

| 역할 | 아티팩트 label | 의미 |
|---|---|---|
| raw return panel | `panel_daily.parquet` | 일별 raw log return 원천 패널 |
| eligibility gate | `eligibility.parquet` | 월별 `liquid_core` 적격성 gate 원장 |
| peer source panel | `raw_nn_peer_l30.parquet` | window×ticker 기준 peer 회귀 기본 패널 |
| summary stats | `raw_top3_return_summary_l30.csv` | metric별 per-stock 분포 요약 |
| per-stock panel | `raw_top3_return_per_stock_l30.csv` | ticker별 점수 패널 |
| top/bottom sample | `raw_top3_return_top30_l30.csv`, `raw_top3_return_bottom30_l30.csv` | 상·하위 샘플 |
| pooled stats | `raw_top3_return_pooled_l30.csv` | 전 ticker-window를 합산한 pooled 점수 |
| distribution figure | `raw_top3_return_distribution_l30.png` | per-stock 분포 비교 그림 |

<details><summary>예시 JSON: 입력/출력 산출물 스냅샷</summary>

```json
{
  "peer_source_panel": {
    "test_windows": 62,
    "window_ticker_rows": 60960,
    "unique_tickers": 1687
  },
  "summary_stats": {
    "metric": "oos_r2_ew3",
    "n": 1119,
    "mean": 0.17496592847595605,
    "p50": 0.13963469491300673,
    "positive_share": 0.7676496872207328
  },
  "pooled_stats": {
    "n_stocks": 1119,
    "n_window_ticker_rows": 60960,
    "pooled_oos_r2_free3": 0.12644277228077472,
    "pooled_oos_r2_ew3": 0.14893661165230232,
    "pooled_oos_r2_rand3": -0.04449572594732709,
    "mean_in_window_r2": 0.7148133188882713
  },
  "top30_example": {
    "ticker": "IMAB",
    "oos_r2_free3": 0.9999999998932677,
    "avg_corr": 0.7990876882771257
  },
  "bottom30_example": {
    "ticker": "SILK",
    "oos_r2_free3": -0.6785775506684473,
    "avg_corr": 0.6898811708426642
  }
}
```

</details>

## Results

근거는 `근거/출처`의 summary stats, pooled stats generated CSV를 따른다.

### 결과 표 필드 해설

| 필드 | 의미 |
|---|---|
| `mean` | 1,119개 per-stock 점수의 평균 |
| `median` / `p50` | 중앙값 |
| `p75`, `p90` | 상위 분위수 |
| `positive share` | 해당 지표가 0보다 큰 ticker 비중 |
| `pooled OOS R²` | 모든 window-ticker row를 한 번에 합산해 계산한 설명력 |

<details><summary>예시 JSON: 결과 표 해석용 실제 값</summary>

```json
{
  "mean_example": {
    "metric": "oos_r2_ew3",
    "value": 0.17496592847595605
  },
  "median_example": {
    "metric": "oos_r2_free3",
    "value": 0.11147107413310808
  },
  "p90_example": {
    "metric": "oos_r2_free3",
    "value": 0.5796484109728595
  },
  "positive_share_example": {
    "metric": "oos_r2_rand3",
    "value": 0.3735478105451296
  },
  "pooled_oos_r2_example": {
    "metric": "equal_weight_top3",
    "value": 0.14893661165230232
  }
}
```

</details>

### 1) 핵심 성과 요약

| 지표 | mean | median | p75 | p90 | positive share |
|---|---:|---:|---:|---:|---:|
| In-window R² | **71.4%** | **70.0%** | 74.9% | 82.0% | 100.0% |
| OOS R² (free 3-coef) | **16.2%** | **11.1%** | **29.2%** | **58.0%** | **69.2%** |
| OOS R² (equal-weight top-3) | **17.5%** | **14.0%** | **30.8%** | **53.1%** | **76.8%** |
| OOS R² (random-3 placebo) | **-3.8%** | **-3.1%** | 3.6% | 8.7% | **37.4%** |

<details><summary>예시 JSON: 핵심 성과 요약 실제 값</summary>

```json
{
  "in_r2": {
    "mean": 0.7140387873873635,
    "median": 0.6999981901275493,
    "p75": 0.7491657869061615,
    "p90": 0.8195186884489722,
    "positive_share": 1.0
  },
  "oos_r2_free3": {
    "mean": 0.1615461683400292,
    "median": 0.11147107413310808,
    "p75": 0.29225495699687304,
    "p90": 0.5796484109728595,
    "positive_share": 0.6916890080428955
  },
  "oos_r2_ew3": {
    "mean": 0.17496592847595605,
    "median": 0.13963469491300673,
    "p75": 0.3080340614915681,
    "p90": 0.531022263789452,
    "positive_share": 0.7676496872207328
  },
  "oos_r2_rand3": {
    "mean": -0.03803768737234256,
    "median": -0.030531847111028743,
    "p75": 0.036384300895548394,
    "p90": 0.08706649088494936,
    "positive_share": 0.3735478105451296
  }
}
```

</details>

### 2) pooled 요약

| 표본 | pooled OOS R² |
|---|---:|
| free 3-coef | **12.64%** |
| equal-weight top-3 | **14.89%** |
| random-3 placebo | **-4.45%** |

<details><summary>예시 JSON: pooled 요약 실제 값</summary>

```json
{
  "n_stocks": 1119,
  "n_window_ticker_rows": 60960,
  "pooled_oos_r2_free3": 0.12644277228077472,
  "pooled_oos_r2_ew3": 0.14893661165230232,
  "pooled_oos_r2_rand3": -0.04449572594732709
}
```

</details>

### 3) peer 구조 요약

| 보조 지표 | mean | 해석 |
|---|---:|---|
| 평균 peer 상관 (`avg_corr`) | **73.6%** | 30일 창에서 선택된 peer들은 raw return 기준으로 강하게 동행 |
| 같은 sub-industry 비중 (`same_sub`) | **26.8%** | nearest peer가 항상 같은 세부 업종인 것은 아님 |
| 같은 sector 비중 (`same_sec`) | **45.9%** | sector 공통요인이 상당 부분 남아 있음 |

<details><summary>예시 JSON: peer 구조 요약 실제 값</summary>

```json
{
  "avg_corr": 0.7364884155309035,
  "same_sub": 0.2678467090253442,
  "same_sec": 0.4587931677732791
}
```

</details>

## Interpretation

- **raw return은 설명력이 더 높다.** FF5 잔차를 제거하지 않았기 때문에 시장·섹터·스타일 공통요인이 그대로 남아 있고, 그 결과 top-3 peer의 contemporaneous 설명력이 높게 나온다.
- **단순 등가중이 자유계수보다 약간 낫다.** 평균 기준 `17.5% > 16.2%`, pooled 기준 `14.89% > 12.64%`다. 이 결과는 30일 창에서 단순화된 weighting이 더 안정적일 수 있음을 시사한다.
- **placebo가 음수다.** 무작위 3종목은 평균 `-3.8%`, pooled `-4.45%`라서 아무 3종목을 넣는다고 같은 설명력이 나오지는 않는다.
- **하지만 이것을 예측력으로 읽으면 안 된다.** 이 수치는 같은 날 peer 수익률로 같은 날 대상 수익률을 설명한 attribution이다. 따라서 높은 `in_r2`나 양의 `oos_r2_*`를 그대로 tradable alpha로 해석할 수 없다.

## Limitations

- **FF5 residual 아님**: raw log return이라 공통 시장노출이 섞여 있다.
- **동시점 설명**: same-day peer return을 쓰므로 ex-ante 예측 성능과 다르다.
- **30d only**: lookback 민감도, horizon 민감도, regime 민감도는 이 보고서가 다루지 않는다.
- **구조 해석 제한**: `same_sub`가 26.8%에 그쳐, 설명력이 산업동행인지 다른 공통충격인지 추가 분해가 필요하다.

## Next checks

1. 같은 프로토콜로 **FF5 residual 버전**과 raw-return 버전을 나란히 비교해 공통요인 제거 전후 차이를 정량화한다.
2. **lookback 21/63/126/252d sweep**으로 30일 결과가 얼마나 안정적인지 확인한다.
3. peer 입력을 **lagged return**으로 바꿔 contemporaneous attribution이 아니라 **tradable prediction**으로 다시 채점한다.
4. sector / sub-industry / market-cap 버킷별로 분해해 어떤 구간에서 raw peer 효과가 강한지 확인한다.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| source parquet | `../../../data/processed/analytics/cluster_research/data/panel_daily.parquet`, `../../../data/processed/analytics/cluster_research/data/eligibility.parquet` | raw return 원천 패널과 `liquid_core` gate 입력 |
| peer source panel | `../../../data/processed/analytics/cluster_research/data/raw_nn_peer_l30.parquet` | 60,960 window-ticker rows, 62개 테스트 기간, 1,687개 고유 ticker 근거 |
| generated CSV | `../../../data/processed/analytics/cluster_research/outputs/raw_top3_return_summary_l30.csv`, `../../../data/processed/analytics/cluster_research/outputs/raw_top3_return_per_stock_l30.csv`, `../../../data/processed/analytics/cluster_research/outputs/raw_top3_return_top30_l30.csv`, `../../../data/processed/analytics/cluster_research/outputs/raw_top3_return_bottom30_l30.csv`, `../../../data/processed/analytics/cluster_research/outputs/raw_top3_return_pooled_l30.csv` | per-stock 분포, 상·하위 샘플, pooled 결과 |
| figure | `../../../artifacts/analytics/cluster_research/reports/figures/raw_top3_return_distribution_l30.png` | 분포 시각화 |
| method docs | `../../../docs/archive/research/ff5-cluster-pilot/07-nearest-peer-regression.md`, `../../../docs/archive/research/ff5-cluster-pilot/README.md` | one-off 분석 맥락과 기존 설명 문서 |
| gate implementation | `../../../research/analytics/cluster_research/config.py`, `../../../research/analytics/cluster_research/windows.py` | `liquid_core` 적격 조건과 월별 gate 계산 경로 |
