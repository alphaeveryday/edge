---
doc_type: experiment
status: Draft
owner: price-research
created: 2026-07-10
updated: 2026-07-21
related:
  - ../STATE.md
  - ../hypotheses.md
  - ../decisions/RDR-001-factor-selection.md
  - ../../../engineering/specs/price-decomposition-engine.md
  - EXP-001-raw-top3-peer-regression.md
---
# EXP-002: 시장·피어 2-leg 모델 검증 (시장직교 peer + 룩백 스윕)

> 한 줄 결론: **시장 leg에 직교화한 top-3 peer leg는 시장-only 대비 OOS 동시점 설명력을 유의하게 높이며(N=252에서 +11.5%p, 95% CI 제외 0), 룩백은 길수록 좋아 학습창 상한인 252거래일이 최적이다.** peer 후보를 **동일 GICS로 제한하면 짧은·중간 룩백(N≤126)에서 설명력이 유의하게 개선**되지만, 최적 룩백 252일에서는 이득이 사라진다(무제한 선택이 이미 동업종으로 수렴). **우선주 제외는 이 유니버스에서 무효과**(적격 풀에 우선주 0종).

## Summary

시장 수익률(`mkt_rf`)과 그것에 직교화한 exact-3 peer 바스켓의 2-leg 회귀를, 직전 `look`거래일에서 계수를 적합해 그 다음 거래일에 **strictly OOS**로 채점했다. 62개 테스트 기간·약 128만 window-ticker-day에서 룩백 N을 {10…252} 스윕하고, peer 후보 풀을 (a) 무제한(EXP-001 기준선), (b) 동일 `gics_sub_industry`(세), (c) 동일 `gics_industry`(소) — 모두 **우선주 제외·다른 issuer** — 세 갈래로 비교했다. 검증으로 무제한·N=30 설정이 EXP-001의 raw ew3(0.1489), avg_corr(0.738), same_sub(0.268)을 재현함을 확인했다.

## Question

1. **모델**: 시장 leg + **시장직교 peer leg**를 함께 쓸 때 OOS 설명력(R²)은 얼마이고, 그중 **peer 직교 성분만의 증분 설명력**은 얼마인가?
2. **제약**: peer3를 **우선주가 아니고 GICS 소분류가 같은** 후보로만 제한하면, 제한하지 않을 때와 OOS 성능이 달라지는가?
3. **룩백**: 과거 며칠(N) 룩백이 최적인가?

## Model (검증한 정확한 수식)

종목 $i$·거래일 $t$에서, 계수는 직전 $N$거래일 창 $[t-N, t-1]$에서 적합하고 $t$일에 OOS 채점한다(peer 값은 동시점 사용 → tradable 예측이 아니라 attribution).

$$r_1 = r_0 - \beta_m r_m - a_m \qquad \tfrac{|r_1|}{\sigma_{r_1}} < 2 \Rightarrow \textbf{market}$$

$$r_{\perp p} = \Big(\textstyle\sum_{k=1}^{3} w_k\, p_k\Big) - \beta_{nm} r_m - a_{nm}\qquad(\text{peer 바스켓을 시장에 직교화})$$

$$r_2 = r_1 - \beta_{\perp p}\, r_{\perp p} - a_{\perp p}\qquad \tfrac{|r_2|}{\sigma_{r_2}} < 2 \Rightarrow \textbf{thema},\ \ \ge 2 \Rightarrow \textbf{concentrated}$$

- $r_0$: 대상 raw 로그수익률, $r_m=$ `mkt_rf`(일별, decimal). 가중치 $w_k=\tfrac13$ 등가중(EXP-001에서 ew3 ≳ free3).
- $r_{\perp p}$는 창 안에서 $[1, r_m]$에 직교이므로(FWL) $r_1$을 $r_{\perp p}$에 회귀한 것은 $r_0 \sim [1, r_m, r_{\perp p}]$ 결합회귀와 동치다.
- 설명력(pooled OOS, SSE 합산): $\text{SSE}_0=\sum r_0^2,\ \text{SSE}_1=\sum r_1^2,\ \text{SSE}_2=\sum r_2^2$
  - $R^2_{market}=1-\text{SSE}_1/\text{SSE}_0$
  - $R^2_{market+peer\perp}=1-\text{SSE}_2/\text{SSE}_0$
  - $\Delta R^2_{peer\perp}=R^2_{combined}-R^2_{market}=(\text{SSE}_1-\text{SSE}_2)/\text{SSE}_0$ ← **peer 직교만의 증분**

## Method

- 워크포워드: EXP-001과 동일한 월별 창(train 252 / val 21 / test = 그 달), 62개 테스트 기간. 적격성은 기존 `liquid_core` gate(`eligibility.parquet`) 재사용.
- 공정 비교: 창마다 pre = train+val의 마지막 252일 + test로 **한 번만** 피벗(winsor 0.1%)하고, **모든 N을 같은 test일에** 채점(EXP-001의 per-look 시프트 개선).
- peer 선택: 창 raw수익률 상관 top-3(대각·동일 issuer 제외). 제약 갈래는 후보 마스크에 `동일 GICS`·`비우선주`를 추가.
- 우선주 판정: `company_name` 정규식(preferred/pfd/depositary…preferred/% cumulative/series X preferred). `Preferred Bank`(보통주)·`American Depositary Shares`(ADR) 오탐 제외.
- placebo: random-3(다른 issuer) 동일 분해. **유의성 검정단위 = 62개 테스트월**(1.2M stock-day를 독립처럼 쓰면 유의성 과장). month-block 부트스트랩 CI·two-sided ASL p(10,000회) + 월별 paired Wilcoxon signed-rank + 월별 paired t + BH 다중비교 보정 — 세 방법이 일치.
- 검증: 무제한·N=30이 EXP-001 raw 산출물(`raw_nn_peer_l30.parquet`)을 재현 → **ew3=0.1482 (목표 0.1489)**, avg_corr=0.738, same_sub=0.268.

## Results

전 수치는 pooled OOS(62기간, 약 1.276M stock-day). 무제한 기준.

### 1) 룩백 스윕 — 무제한(unrestricted)

| N(거래일) | R²_market | ΔR²_peer⊥ | R²_market+peer⊥ | route(mkt/thema/conc) | avg_corr | same_sub |
|---:|---:|---:|---:|---|---:|---:|
| 10  | -1.7% | -8.6% | -10.3% | 86.7 / 1.4 / 12.0 | 0.891 | 0.169 |
| 21  | 10.8% | -4.8% | 6.0%  | 91.6 / 1.5 / 6.9 | 0.782 | 0.241 |
| 30  | 13.5% | -1.8% | 11.7% | 92.9 / 1.5 / 5.7 | 0.738 | 0.268 |
| 42  | 15.3% | +0.7% | 16.1% | 93.7 / 1.5 / 4.8 | 0.701 | 0.290 |
| 63  | 16.6% | +4.1% | 20.7% | 94.5 / 1.4 / 4.1 | 0.663 | 0.313 |
| 90  | 17.4% | +6.2% | 23.6% | 94.9 / 1.4 / 3.7 | 0.633 | 0.334 |
| 126 | 17.8% | +8.1% | 25.9% | 95.1 / 1.4 / 3.5 | 0.609 | 0.352 |
| 189 | 18.1% | +10.3% | 28.4% | 95.4 / 1.4 / 3.2 | 0.586 | 0.371 |
| **252** | **18.2%** | **+11.5%** | **29.6%** | 95.6 / 1.3 / 3.1 | 0.573 | 0.383 |

- ΔR²_peer⊥·R²_combined 모두 **N에 단조증가**하며 상한 252에서도 계속 상승. R²_market은 N≈90 이후 ~18%에서 포화.
- N=10은 R²_market이 음수(30일 미만은 베타 과적합), peer⊥ 증분은 N≥42에서야 양수.

### 2) 최적 N과 갈래 비교 (N=252, own-sample pooled, 95% month-block CI)

| 갈래 | 커버리지 | R²_market | **ΔR²_peer⊥ [95% CI]** | R²_market+peer⊥ |
|---|---:|---:|---:|---:|
| unrestricted | 100.0% | 18.2% | **+11.46% [+9.79, +13.13]** | 29.6% |
| 동일 sub-industry(세) | 92.0% | 18.3% | **+11.22% [+9.89, +12.54]** | 29.6% |
| 동일 industry(소) | 97.5% | 18.2% | **+11.62% [+10.24, +12.98]** | 29.8% |

- **최적 N = 252(모든 갈래·모든 지표에서 argmax; per-stock 중앙값 기준도 252).** 곡선이 학습창 상한까지 단조 상승하므로 실효 최적은 “가능한 가장 긴 룩백(≈학습창 252일)”.
- own-sample 기준 N=252에서 세 갈래의 증분은 **사실상 동일**(±0.3%p). 동일 industry(소)가 세보다 근소 우위이면서 커버리지 손실이 작음(2.5% vs 8%).

### 3) 제약 효과 — 동일 표본 paired 비교 (ΔR²_peer⊥ 차이, restricted − unrestricted, 95% CI, %p)

| N | 동일 sub-industry(세) − 무제한 | 동일 industry(소) − 무제한 |
|---:|---:|---:|
| 21  | **+3.01 [+1.99, +4.04]** ✓ | **+3.08 [+2.06, +4.14]** ✓ |
| 30  | **+3.01 [+2.34, +3.72]** ✓ | **+3.33 [+2.52, +4.14]** ✓ |
| 63  | **+2.05 [+1.49, +2.66]** ✓ | **+2.36 [+1.85, +2.91]** ✓ |
| 126 | **+0.97 [+0.46, +1.52]** ✓ | **+1.47 [+0.99, +2.00]** ✓ |
| 252 | −0.32 [−0.78, +0.16] | +0.19 [−0.21, +0.63] |

✓ = 95% CI가 0을 제외(유의). **GICS 제약은 N=63~126에서만 유의하게 이득**(부트스트랩 ASL p·월별 Wilcoxon·월별 t 모두 < 0.001, BH 보정 후에도 유의). **최적 N=252에서는 이득이 통계적으로 소멸**(소: boot_p 0.36 / wilcox 0.22 / t 0.22; 세: boot_p 0.19 / wilcox 0.35 / t 0.36 — 전부 p ≫ 0.05). N≤30은 paired 차이는 유의하나 **기저 peer⊥ leg가 0과 구분되지 않음**(own-sample ΔR²_peer⊥: N=30 소/세 p 0.19~0.55, N≥63부터 기저도 유의 p<0.001). 즉 실질 개선은 중간 룩백에 한정되고 채택 운영점(252일)에서는 무효과.

### 4) 우선주(preferred) 제외 효과

- 적격 `liquid_core` peer 풀에서 우선주 판정 **0종**(62기간 합). issuer_id가 이미 동일사 우선주·복수클래스를 제외하고, NASDAQ 유동성 gate가 우선주를 걸러냄. → **우선주 제외 제약은 이 유니버스에서 무효과.** 구현은 유지(다른 유니버스·향후 재사용 대비).

### 5) placebo

random-3 peer⊥ 증분은 전 구간 0 이하(N=252 −2.5%p). 선택된 peer의 증분(+11.5%p)과 명확히 분리.

## Interpretation

- **H-002 지지**: 시장직교 peer leg는 시장 중복을 제거하고도 OOS 설명력을 **+11.5%p**(N=252, CI 제외 0) 더한다. 시장 leg 18.2% + peer⊥ 11.5%p ≈ **결합 29.6%**의 동시점 설명.
- **룩백은 길수록 좋다**: peer 선택·베타·직교화 모두 표본이 길수록 안정. 이득은 주로 peer⊥ leg에서 나온다(R²_market은 90일 이후 포화, R²_combined는 252까지 계속 상승).
- **GICS 제약의 역할은 “짧은 룩백의 보험”**: 창이 짧아 상관 추정이 불안정할 때 동업종 prior가 peer 품질을 지켜 유의하게 개선(+2~3%p). 룩백이 252일이면 무제한 상관선택이 이미 동업종으로 수렴(same_sub 0.17→0.38)해 제약이 잉여가 된다.
- **정책 함의**: 최적 운영점(N=252)에서는 **동일 industry(소) 제약**이 무난하다 — 증분은 무제한과 통계적으로 동일하면서, 잘못된 교차업종 peer를 배제하고 커버리지 손실이 2.5%로 작다. 세(sub-industry)까지 좁히면 커버리지 8% 손실 대비 추가 이득 없음. 짧은 룩백을 쓸 수밖에 없는 신규상장·짧은 이력 종목엔 GICS 제약이 특히 유효.
- route: 2σ 게이트에서 대부분 “market”(N=252 95.6%), thema 1.3%, concentrated 3.1%. 룩백이 길수록 σ 추정이 안정돼 market 비중↑·concentrated↓.

## Limitations

- **동시점 attribution**: peer 값을 같은 날 사용 → ex-ante 예측력 아님(EXP-001과 동일 제약).
- **252일 상한**: train=252라 pre 창이 273일뿐. N>252는 파이프라인상 불가 → “252가 최적”은 곧 “학습창 상한이 최적”. 더 긴 룩백 검증엔 train 확장 필요.
- **우선주 무효과는 유니버스 특수성**: NASDAQ liquid_core 결과. KR/NYSE 등 우선주 풍부한 유니버스에선 재검증 필요.
- **market=mkt_rf**: Ken-French 시장초과수익 사용(절편이 rf 흡수). 지수(KOSPI200 등) 벤치마크로 바꾸면 R²_market 수준은 달라질 수 있으나 peer⊥ 증분의 방향성 결론은 스케일 불변.
- **turnover 미측정**: peer-set 갱신 주기·계수 안정성(H-002 후반부 지표)은 이 실험 범위 밖.

## Next checks

1. peer-set turnover·계수 안정성 정량화(월별 갱신 vs 고정 주기)로 H-002 나머지 절 마감.
2. lagged peer 입력으로 재채점해 tradable prediction 여부 분리(H-003).
3. train 확장으로 N>252 구간 확인(단조 상승이 어디서 꺾이는지).
4. residual gate(2σ) 통과율과 중요 이벤트 recall 연결(H-003 게이트 절).
5. ETF 구성종목 적용 시 소형 구성종목의 exact-3 동업종 충족률(커버리지 8%/2.5% 손실의 구성종목 편중 확인).

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| engine | `../../../research/analytics/cluster_research/r09_peer_orthogonal_decomp.py` | 시장→peer⊥ OOS 분해·룩백 스윕·갈래·placebo·paired |
| analysis | `../../../research/analytics/cluster_research/r09_analyze.py`, `.../r09_ci.py` | 최적 N·갈래표·month-block CI·그림 |
| source parquet | `../../../data/processed/analytics/cluster_research/data/panel_daily.parquet`, `.../ff5_daily.parquet`, `.../universe_gics.parquet`, `.../eligibility.parquet` | raw수익률·시장factor·GICS/issuer·적격 gate |
| generated | `../../../data/processed/analytics/cluster_research/outputs/peer_orthogonal_decomp_rows.parquet`, `.../peer_orthogonal_pooled.csv`, `.../peer_orthogonal_perstock.csv`, `.../peer_orthogonal_perperiod.parquet` | per-stock·pooled·per-period 산출 |
| figure | `../../../artifacts/analytics/cluster_research/reports/figures/peer_orthogonal_lookback_sweep.png` | 룩백 스윕(peer⊥ 증분·결합 R²) |
| validation ref | `../../../data/processed/analytics/cluster_research/data/raw_nn_peer_l30.parquet` | EXP-001 raw ew3=0.1489 재현 대조 |
