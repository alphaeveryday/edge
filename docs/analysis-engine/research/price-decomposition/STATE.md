---
doc_type: state
status: Draft
owner: price-research
created: 2026-07-10
updated: 2026-07-10
related:
  - hypotheses.md
  - experiments/EXP-001-raw-top3-peer-regression.md
  - experiments/EXP-002-market-peer-model.md
  - decisions/RDR-001-factor-selection.md
---
# 가격 분해 연구 상태

| 항목 | 현재 상태 |
|---|---|
| 현재 결론 | 가격 설명축은 시장 수익률과 시장에 직교화한 exact-3 피어 수익률의 2-leg 구조로 제한한다. 적용 레벨은 개별 종목(ETF 구성종목 포함)이며, ETF 레벨 1차 분해는 구성종목 기여 항등식이 소유한다. |
| 신뢰도 | 중상. EXP-002에서 시장직교 peer leg가 시장-only 대비 OOS 동시점 설명력을 **+11.5%p**(N=252, 95% CI [+9.8, +13.1], 0 제외) 유의하게 높임을 62기간 약 128만 stock-day로 통합 검증. 무제한·N=30이 EXP-001(ew3 0.1489)을 재현. |
| 가장 강한 근거 | EXP-002: 시장 통제 후에도 peer⊥ 증분 +11.5%p(random-3 placebo는 전 구간 0 이하), 결합 R² 29.6%. 룩백은 단조증가로 학습창 상한 252일이 최적. GICS 제약은 N≤126에서 +1~3.3%p 유의 개선. |
| 가장 큰 주의점 | 동시점 설명력은 예측력이나 인과성이 아니다. 30일 실험 결과를 252일 정책의 직접 증거로 확대하면 안 된다. |
| 다음 행동 | peer-set turnover·계수 안정성 정량화, lagged 입력으로 예측력 분리, train 확장으로 N>252 확인, residual 2σ gate의 이벤트 recall 검증. 우선주 제외는 이 유니버스에서 무효과(적격 풀 0종). |

## Active decisions

- 시장 + 시장 직교 피어만 사용한다.
- 피어는 직전 252거래일 상관 top-3, 동일 GICS theme 또는 sub-industry 안에서 선택한다.
- 잔차는 `price-only unexplained move`로 표현한다.
- 잔차 유의성은 이벤트 분석 진입 게이트이며 원인 판정이 아니다.
- 2-leg 모델의 적용 레벨은 개별 종목이다. ETF 레벨은 항등식(기여·괴리·환율) 분해가 선행한다.

## Open risks

- 피어 세트 갱신 주기와 변동성 레짐 민감도
- 252일 룩백이 신규 상장·구조 변화 종목에 불리할 가능성
- 시장·피어 외 설명축을 제거했을 때의 잔차 편향
- 잔차 임계값이 이벤트 recall과 분석 비용 사이에서 만드는 trade-off
- ETF 구성종목에 적용할 때 소형 구성종목의 exact-3 피어 충족률
