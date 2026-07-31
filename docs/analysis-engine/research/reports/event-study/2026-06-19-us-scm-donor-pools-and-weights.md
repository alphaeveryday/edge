---
doc_type: report
status: Accepted
owner: research
created: 2026-07-11
updated: 2026-07-11
related:
  - ../../research/event-study/us-news-event-study-pca-scm-v4-2.md
---

> **모듈 스펙:** `src/alphamale/analytics/residuals/` + packaged/common event-study tooling이 남긴 정적 donor SCM 산출물 스냅샷.
> **상태:** 결과 스냅샷 — `us_scm_residuals_20260619T114813Z` 1개 런의 도너 가중치·적합 결과(재생성 가능 산출물).
> **주의:** v4.2 공통 PCA-SCM(`common/factor_scm.py`, phase3~5)과는 **별개의 정적-도너 분기**.

# US SCM Donor Pools and Weights

- Source run: `data/processed/analytics/analysis_outputs/ff5_news_residuals/us_scm_residuals_20260619T114813Z`
- Weights file: `scm_weights.parquet` / `scm_weights.csv`
- Comparison file: `comparison_summary.parquet` / `comparison_summary.csv`
- Donor reasoning file: `donor_pool_reasoning.md`
- Constraint: weights are non-negative and sum to 1 per target.

## Fit summary
| ticker   | event_date   | fit_status   |   matched_pre_event_observations |   gap_residual_correlation |   gap_residual_rmse |   mean_post_event_gap |   cumulative_post_event_gap |
|:---------|:-------------|:-------------|---------------------------------:|---------------------------:|--------------------:|----------------------:|----------------------------:|
| AAPL     | 2025-02-24   | ok           |                              161 |                   0.833018 |            0.007988 |             -0.000022 |                   -0.006469 |
| BRK-B    | 2025-05-05   | ok           |                              210 |                   0.529772 |            0.008354 |             -0.000344 |                   -0.085675 |
| CAT      | 2025-10-29   | ok           |                              333 |                   0.792208 |            0.009091 |              0.002773 |                    0.349362 |
| GE       | 2026-03-09   | ok           |                              421 |                   0.844053 |            0.008866 |             -0.004162 |                   -0.158160 |
| JPM      | 2026-01-22   | ok           |                              390 |                   0.679946 |            0.007434 |              0.000317 |                    0.021861 |
| MSFT     | 2025-05-19   | ok           |                              220 |                   0.665664 |            0.010277 |             -0.000716 |                   -0.171179 |
| NVDA     | 2025-03-18   | ok           |                              177 |                   0.645270 |            0.016691 |             -0.001173 |                   -0.330760 |
| RTX      | 2026-02-04   | ok           |                              399 |                   0.665353 |            0.011192 |             -0.000243 |                   -0.014586 |
| V        | 2025-04-30   | ok           |                              207 |                   0.494705 |            0.010120 |              0.000093 |                    0.023320 |

## AAPL

- Event date: `2025-02-24` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `161`
- Gap vs FF5 residual correlation: `0.833018`
- Gap/residual RMSE: `0.007988`

| donor_ticker   |   weight | donor_reason                                                                      |
|:---------------|---------:|:----------------------------------------------------------------------------------|
| CSCO           | 0.273428 | Scaled hardware and enterprise networking peer with long history.                 |
| META           | 0.226408 | Large-cap internet platform with strong advertising-led risk appetite signal.     |
| GOOGL          | 0.207373 | Mega-cap platform peer with durable US tech market beta.                          |
| CRM            | 0.155673 | Application software exposure keeps the pool from overfitting handset cycles.     |
| ADBE           | 0.105415 | Large software franchise with stable profitability and low idiosyncratic overlap. |
| AMZN           | 0.031702 | Consumer-tech and cloud exposure broadens the basket beyond devices.              |
| ORCL           | 0.000000 | Enterprise software incumbent that helps anchor non-hardware tech beta.           |

## BRK-B

- Event date: `2025-05-05` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `210`
- Gap vs FF5 residual correlation: `0.529772`
- Gap/residual RMSE: `0.008354`

| donor_ticker   |   weight | donor_reason                                                                |
|:---------------|---------:|:----------------------------------------------------------------------------|
| AIG            | 0.277432 | Large insurance balance-sheet exposure broadens the donor pool.             |
| PGR            | 0.241897 | Property and casualty insurer contributes underwriting-cycle exposure.      |
| BLK            | 0.174060 | Scaled asset-management franchise that anchors mega-cap financial beta.     |
| MKL            | 0.147148 | Insurance-driven conglomerate closest to Berkshire's public analogue.       |
| ALL            | 0.129581 | Retail insurance exposure adds diversification within the financial basket. |
| APO            | 0.029881 | Diversified financial asset exposure approximates capital-allocation beta.  |
| BX             | 0.000000 | Large alternative-asset manager with broad macro sensitivity.               |

## CAT

- Event date: `2025-10-29` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `333`
- Gap vs FF5 residual correlation: `0.792208`
- Gap/residual RMSE: `0.009091`

| donor_ticker   |   weight | donor_reason                                                           |
|:---------------|---------:|:-----------------------------------------------------------------------|
| DE             | 0.228029 | Heavy equipment peer tied to industrial capex and cyclicality.         |
| URI            | 0.199841 | Construction-equipment demand proxy that helps capture capex shocks.   |
| IR             | 0.154093 | Industrial equipment peer with multi-cycle demand exposure.            |
| CMI            | 0.136742 | Engine and powertrain peer with comparable macro sensitivity.          |
| ETN            | 0.122114 | Electrification and industrial systems diversify the machinery basket. |
| PCAR           | 0.118426 | Truck and equipment exposure tracks industrial demand regimes.         |
| PH             | 0.040755 | Industrial motion-control peer with durable operating leverage.        |

## GE

- Event date: `2026-03-09` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `421`
- Gap vs FF5 residual correlation: `0.844053`
- Gap/residual RMSE: `0.008866`

| donor_ticker   |   weight | donor_reason                                                             |
|:---------------|---------:|:-------------------------------------------------------------------------|
| TT             | 0.268624 | Capital goods peer with durable multi-industry demand exposure.          |
| PH             | 0.249836 | Motion and aerospace systems exposure complements broad industrial beta. |
| ETN            | 0.190653 | Power systems and electrification exposure overlaps key GE end markets.  |
| HON            | 0.162764 | Diversified industrial and aerospace systems peer.                       |
| MMM            | 0.128124 | Diversified industrial exposure widens the donor basket.                 |
| ITW            | 0.000000 | Industrial process peer with stable profitability.                       |
| EMR            | 0.000000 | Industrial automation peer with long public history.                     |

## JPM

- Event date: `2026-01-22` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `390`
- Gap vs FF5 residual correlation: `0.679946`
- Gap/residual RMSE: `0.007434`

| donor_ticker   |   weight | donor_reason                                                           |
|:---------------|---------:|:-----------------------------------------------------------------------|
| GS             | 0.354031 | Capital-markets exposure broadens the bank mix.                        |
| BAC            | 0.297967 | Large universal-bank peer with similar rate sensitivity.               |
| BK             | 0.135826 | Custody-bank exposure adds non-lending financial beta.                 |
| WFC            | 0.127586 | Large domestic banking peer with comparable credit sensitivity.        |
| USB            | 0.060244 | Regional-super-regional bridge with cleaner domestic lending exposure. |
| MS             | 0.015063 | Wealth and investment-bank exposure stabilizes the donor basket.       |
| C              | 0.009284 | Global bank exposure captures money-center macro beta.                 |

## MSFT

- Event date: `2025-05-19` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `220`
- Gap vs FF5 residual correlation: `0.665664`
- Gap/residual RMSE: `0.010277`

| donor_ticker   |   weight | donor_reason                                                             |
|:---------------|---------:|:-------------------------------------------------------------------------|
| GOOGL          | 0.370451 | Mega-cap tech benchmark without using another target ticker.             |
| ORCL           | 0.155131 | Enterprise software peer with long-lived business-model similarity.      |
| NOW            | 0.154057 | Workflow software exposure captures enterprise IT demand swings.         |
| INTU           | 0.145356 | Large-cap software name with resilient fundamentals.                     |
| CRM            | 0.084573 | Application software peer with recurring revenue behavior.               |
| ADBE           | 0.075940 | High-margin software franchise helps match quality and duration factors. |
| IBM            | 0.014493 | Older enterprise tech exposure broadens the factor mix.                  |

## NVDA

- Event date: `2025-03-18` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `177`
- Gap vs FF5 residual correlation: `0.645270`
- Gap/residual RMSE: `0.016691`

| donor_ticker   |   weight | donor_reason                                                                        |
|:---------------|---------:|:------------------------------------------------------------------------------------|
| QCOM           | 0.324091 | Scaled fabless semiconductor peer with persistent market beta.                      |
| MRVL           | 0.226403 | Datacenter and networking semiconductor peer.                                       |
| AVGO           | 0.168646 | Large diversified chip supplier with AI-networking sensitivity.                     |
| AMD            | 0.137654 | Close semiconductor and accelerator exposure without reusing another target ticker. |
| MU             | 0.124375 | Memory-cycle sensitivity captures part of the AI hardware demand regime.            |
| INTC           | 0.018831 | Legacy compute exposure helps match broad semiconductor drawdowns.                  |
| TXN            | 0.000000 | Mature analog semiconductor exposure that stabilizes the basket.                    |

## RTX

- Event date: `2026-02-04` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `399`
- Gap vs FF5 residual correlation: `0.665353`
- Gap/residual RMSE: `0.011192`

| donor_ticker   |   weight | donor_reason                                                              |
|:---------------|---------:|:--------------------------------------------------------------------------|
| NOC            | 0.380553 | Large defense systems peer with long program cycles.                      |
| GD             | 0.267455 | Defense prime with diversified platform exposure.                         |
| HEI            | 0.206611 | Commercial and defense aerospace components peer.                         |
| TXT            | 0.068823 | Aerospace and defense manufacturer that broadens the peer mix.            |
| LHX            | 0.040160 | Mission systems and avionics exposure tracks defense demand.              |
| LMT            | 0.035765 | Prime defense contractor and closest listed peer.                         |
| HII            | 0.000633 | Defense shipbuilding exposure helps avoid single-subsector concentration. |

## V

- Event date: `2025-04-30` (phase2_max_news_day)
- Fit status: `ok`
- Pre-event matched observations: `207`
- Gap vs FF5 residual correlation: `0.494705`
- Gap/residual RMSE: `0.010120`

| donor_ticker   |   weight | donor_reason                                                                                                              |
|:---------------|---------:|:--------------------------------------------------------------------------------------------------------------------------|
| MA             | 0.803228 | Closest global card-network peer and expected highest-weight donor.                                                       |
| GPN            | 0.083875 | Merchant-acquiring exposure adds transaction-volume sensitivity.                                                          |
| JKHY           | 0.069349 | Reliable payments-processing and bank-tech exposure diversifies the network-centric names without reusing another target. |
| AXP            | 0.040589 | Payments franchise with consumer-spend sensitivity.                                                                       |
| COF            | 0.002959 | Consumer payments and card-credit exposure broadens the basket.                                                           |
| PYPL           | 0.000000 | Digital-payments exposure captures fintech-led sentiment shifts.                                                          |
| FIS            | 0.000000 | Payments infrastructure peer with long history.                                                                           |
