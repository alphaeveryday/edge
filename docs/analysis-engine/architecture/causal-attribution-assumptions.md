---
doc_type: contract
status: Living
owner: engineering
created: 2026-07-28
updated: 2026-07-28
related:
  - causal-attribution-phase1.md
  - causal-attribution-e2e-findings.md
  - causal-attribution-value-proof.md
  - ../research/explanation/prediction-pipeline-v1.md
---
# 인과귀속 실험 — 전제·미상·편향 대장

> **이 문서를 먼저 읽지 않고 실험을 설계하지 마라.**
>
> 1회전 실패의 원인은 대수나 모델이 아니라 **설계자가 심은 편향**이었다.
> 매번 "측정했다" 고 보고했으나 측정 대상이 목적과 달랐다.
> 확인되지 않은 항목은 `[미상]`. **`[미상]` 을 추측으로 메우는 순간 회전이 무효가 된다.**

---

## 0. 폐기 선언 (2026-07-28)

**실험 1(`experiments/causal_v1`)은 전면 무효다.** 사용자 지시.

| 폐기 | 이유 |
|---|---|
| `golden_cell.idio` 값 | 정의를 모른 채 종속시켰다 |
| 219셀 선정 | 선정 기준 불명 — 선택 편의가 모든 통계에 상속 |
| `daum_rel`/`daum_mat`/`mat_typ` 채점 | 인과 정답이 아니다 (§2) |
| AUC 0.587 / 0.791 / 0.762 등 전 수치 | 위 타깃 위에서 잰 것 |

**보존하는 것은 경험적 사실뿐** — §4 데이터 실측, §3 편향 목록.

---

## 1. 대상은 **ETF** 다 · 분해는 **EXP-002 에서 이미 검증 완료**

`prediction-pipeline-v1.md:27` 의 *"ex-ante 팩터 회계(v4.5)"* 라는 표현 탓에
다변량 팩터 회귀로 오해했다. **틀렸다.** 실제는 순차 2단 직교화 + 라우팅이며
**`research/price-decomposition/experiments/EXP-002-market-peer-model.md` 가 SSOT** 다.
미국 1,654종목 × 62 테스트월 × 약 128만 종목일에서 strictly-OOS 검증됐다.

### 검증된 정확한 수식

계수는 직전 $N$ 거래일 창 $[t-N,\,t-1]$ 에서 적합하고 $t$ 일에 OOS 채점한다.

$$r_1 = r_0 - \beta_m r_m - a_m \qquad \tfrac{|r_1|}{\sigma_{r_1}} < 2 \Rightarrow \textbf{market}$$

$$r_{\perp p} = \Bigl(\sum_{k=1}^{3} w_k p_k\Bigr) - \beta_{nm} r_m - a_{nm}, \qquad w_k=\tfrac13$$

$$r_2 = r_1 - \beta_{\perp p} r_{\perp p} - a_{\perp p}
\qquad \tfrac{|r_2|}{\sigma_{r_2}} < 2 \Rightarrow \textbf{thema},\ \ \ge 2 \Rightarrow \textbf{concentrated}$$

$r_{\perp p}$ 가 창 안에서 $[1, r_m]$ 에 직교이므로(FWL) 위는
$r_0 \sim [1, r_m, r_{\perp p}]$ 결합회귀와 **동치**다. 시장 leg 와 이중계상되지 않는다.

**피어는 ETF 보유종목이 아니라 각 구성종목의 피어다.** 보유종목을 그대로 쓰면
$\sum w_i p_i \approx r_0$ 라 $r_2 \to 0$ 이 자명해진다. 구성종목을 그 피어로
치환한 **합성대조군**이라야 2단이 의미를 갖는다.

### 확정된 설정

| 항목 | 값 | 근거 |
|---|---|---|
| 피어 수 | **exact-3** | 수식의 $p_1,p_2,p_3$ |
| 피어 선정 | **창 raw수익률 상관 top-3** · 대각·동일 issuer 제외 | EXP-002 Method |
| 가중치 | **등가중 $1/3$** | EXP-001 에서 ew3 ≳ free3 |
| 룩백 $N$ | **252** — 모든 갈래·지표 argmax. 단조증가라 실효 최적은 "학습창 상한" | 스윕 10~252 |
| 후보 제약 | **동일 GICS industry(소)** | 252 에서 무제한과 통계적 동일(+0.19 [−0.21,+0.63])이면서 커버리지 손실 2.5% (세는 8% 손실·추가이득 0) |
| 게이트 | $2\sigma$ | |

### 검증된 성능 (N=252, pooled OOS)

| | |
|---|---|
| $R^2_{market}$ | **18.2%** (N≈90 이후 포화) |
| $\Delta R^2_{peer\perp}$ | **+11.5%p** [+9.79, +13.13] — CI 0 제외 |
| 결합 | **29.6%** |
| placebo (random-3) | **−2.5%p** — 명확 분리 |
| 유의성 | month-block 부트스트랩 · Wilcoxon · paired t · BH 보정 **3방법 일치** |

### 라우팅 분포 — **이것이 실험 규모를 결정한다**

| $N$ | market | thema | concentrated |
|---:|---:|---:|---:|
| 30 | 92.9% | 1.5% | 5.7% |
| 63 | 94.5% | 1.4% | 4.1% |
| **252** | **95.6%** | **1.3%** | **3.1%** |

**인과귀속 질문은 `concentrated` 층에서만 성립한다.** 그 층이 **3.1%** 다.
`market`/`thema` 셀에 개별 사건 설명을 붙이는 것은 범주 오류다.

### 엔진

`homeserver/alphamale/research/analytics/cluster_research/r09_peer_orthogonal_decomp.py`
(+ `r09_analyze.py` · `r09_ci.py`). **재구현하지 말고 이식하라.**

### KR·ETF 이식에서 재검증 필요 — EXP-002 Limitations 가 이미 명시

| # | 항목 | 내용 |
|---|---|---|
| K1 | 시장 벤치마크 | `mkt_rf`(Ken-French) → KOSPI200 으로 바꾸면 $R^2_{market}$ 수준이 달라짐. peer⊥ 증분 방향성은 스케일 불변 |
| K2 | **우선주** | NASDAQ liquid_core 엔 0종이라 무효과였음. **"KR 등 우선주 풍부한 유니버스에선 재검증 필요"** |
| K3 | **ETF 구성종목 적용** | EXP-002 Next checks **#5** — *"소형 구성종목의 exact-3 동업종 충족률"*. **이번 작업이 정확히 이것** |
| K4 | 동시점 attribution | peer 값을 같은 날 사용 → ex-ante 예측력 아님. tradable 여부는 H-003(lagged) |
| K5 | **GICS 부재** | `universe_gics.parquet` 는 **US 전용(KR 0종)**. KR 업종 분류 소스가 없다 |

### 남은 `[미상]`

| # | 미상 |
|---|---|
| U1 | 보유 스냅샷이 **2026-07-11 하루뿐** — 이전 일자에 쓰면 look-ahead (§4) |
| U2 | ETF 5분봉 375중 2개 — **일간이 시간해상도 상한** |
| U3 | KR 업종 분류 소스 (K5). `kospi200_proxy.sector` 11종이 유일한 후보 — GICS 소분류보다 훨씬 거침 |

---

## 2. `daum_*` 라벨은 인과 정답이 **아니다**

사람 1인의 **사전 판단 태그**다. *"이 뉴스는 중요해 보인다"* 이지
*"이 사건이 그 움직임을 일으켰다"* 가 아니다. 그 인과는 아무도 관측하지 않았다.

**금지:** 가치 함수의 타깃으로 쓰는 것. 1회전에서 이것을 타깃으로 AUC 를 쟀고,
그 결과는 인과귀속이 아니라 **주석자 흉내내기 점수**였다.

---

## 3. 내가 심었던 편향 — 전부 삭제 대상

| # | 심은 것 | 결과 |
|---|---|---|
| B1 | 검정을 $(B,q,\text{agg},h)$ 대수로 **강제** | 검정 가능 명제가 *"뉴스 더미의 개수·모양이 특이한가"* 로 붕괴 |
| B2 | *"파일 쓰지 마라 · 스크립트 짜지 마라 · JSON 하나만"* | 도구·반복 금지 → **단발 호출**을 재고 "에이전트 가치 없음" 이라 보고 |
| B3 | 가설과 검정의 **단절** | 산문은 "수주가 매출에 닿는다", 검정은 `n_roles` 개수 |
| B4 | `max_quantity` 가 `unit_id` 를 버림 | $\max\{1775\text{대},40\%,20\text{억원}\}=1775$ |
| B5 | 카드가 `events[:8]`·`args[:6]` 절단 | 수치 인자 33,028건 중 1,590건(4.8%)만 노출 |
| B6 | 인과 서명 4종을 **생성자에게 제시** | 생성자 가설 공간을 미리 좁힘. 그건 **검정자가 스스로 고를 재료** |
| B7 | 사람 라벨을 타깃으로 채점 | §2 |

### 재발 방지 규칙

1. **생성자**에게는 목적과 도구만. 가설 형식·검정 형식·고려 사항 목록 금지.
2. **검정자**에게는 샌드박스와 목적만. 무엇으로 검증할지는 검정자가 정한다.
3. 하네스는 **실현 가격으로만** 독립 채점.
4. 표현 손실(절단·단위버림)은 전부 결함으로 기록하고 고치거나 명시.

---

## 4. 데이터 실측 — 확인된 것

### ETF

| | 소스 | 실측 |
|---|---|---|
| $r_0$ | `raw/etf/fmp/etf_daily_kr.parquet` | **375 ETF · 812,944행 · 2016-01-04~2026-07-15 · 일간** |
| $w_i$ 보유 | `raw/etf/fmp/etf_holdings_kr.parquet` | **96 ETF · 6,202행 · 스냅샷 `asof=2026-07-11` 단일** |
| | | `asset` 은 `.KS` 접미 포함. 빈 값 494행. ETF당 보유수 중앙 20.5 · 최대 735 |
| ETF 메타 | `raw/etf/fmp/etf_info_kr.parquet` | 375행 (AUM·섹터리스트·상장일 등) |
| $r_m$ | `raw/prices/fmp_daily_kr/indices_daily_kr.parquet` | `KOSPI200.KS` 5,223행 · 2016~ |
| $p_i$ | `raw/prices/fmp_5min/*.parquet` → 일별 집계 | 1,272종목 · 2022-11-10~2026-07-16 · 5분봉 |

**사용 가능 유니버스: 46 ETF** (KR주식 비중 ≥60% **그리고** 보유 가격커버 ≥90%).

### 결함 — 명시하고 간다

| | |
|---|---|
| **보유 스냅샷 1개** | 2026-07-11. 그 이전 일자에 쓰면 look-ahead. **U5** |
| **ETF 일중 없음** | 375중 5분봉 보유 2개. ETF 분석은 **일간**이 상한 |
| 보유 커버 | 96/375 ETF 만 보유 데이터 존재 |

### 사건

| | |
|---|---|
| 저장소 | `interim/events/events_threads.sqlite` (저장소 밖) |
| 규모 | `canonical_event` 39,959 · `event_argument` 76,020 · `event_thread` 2,402 |
| 기간·범위 | **2026-06-01 ~ 07-20** · 53타입 · KR 엔티티 1,565 |
| 품질 | `completeness=partial` **62%** · `novelty=UNKNOWN` **76%** |
| 빈 테이블 | `event_measure` · `event_snapshot` (0컬럼) |

**분석 창은 사건이 병목이다 — 2026-06-01~07-20, 약 35 거래일.**
$\beta$·$\sigma$ 추정은 장기 가격 이력(2022-11~)을 쓸 수 있다.

### 온톨로지

`src/libs/ontology/src/edge_ontology/resources/` — 존재 4층(entity·attribute·relation·process).
`process/types/` 53타입 + `process/lifecycle_models_v0_1.yaml` 20 라이프사이클.
타입당 `roles{required,optional,identity,primary}` · `note`(자매 경계) ·
`quantities{unit_family}` · **`derived{formula}`** (예 `capex_to_mcap = CAPEX_VALUE/market_cap`).

없는 것: `revenue_ttm` — `derived` 일부 계산 불가.
있는 것: `market_cap`·`sector`(11종) — `fmp_5min/kospi200_proxy.parquet` 200행.

### 무용 확인

`processed/analytics/price/*.parquet` 는 **US 티커**(ARM/BKNG/AEP…). 한국 셀에 쓸 수 없다.

---

## 5. 가치 함수 — 관측 가능한 정답은 없다

인과 정답은 **관측되지 않는다**(반사실 부재). 객관 기반은 **실현 가격** 하나뿐.

$$\text{가치}=\frac{1}{|S|}\sum_{c\in S}\log_2\frac{p_{\text{주장}}(y_c)}{p_0(y_c)}\quad[\text{bit/셀}]$$

$y_c$ = 실현 결과, $p_0$ = 무조건부. 적정 채점규칙이라 **확신 부풀리기가 손해**다.
기권은 0 bit — 이득도 손해도 없으므로 "모르면 침묵" 이 허용된다.

$y_c$ 가 구체적으로 무엇인지는 **U1·U6 이 풀려야 정해진다.**

---

## 6. 해소됨 — 피어는 각 구성종목의 피어

한때 *"피어를 ETF 자신의 보유종목으로 두면 $\sum w_i p_i \approx r_0$ 라 $r_2\to0$"* 을
막힘으로 기록했다. 해소: **피어는 보유종목이 아니라 각 구성종목의 상관 top-3** 다.
구성종목을 그 피어로 치환한 합성대조군이므로 $r_2$ 는 자명하게 0이 되지 않는다.
실측으로도 $\Delta R^2_{peer\perp}=+23.2\%p$ 이고 잔차가 남는다(§7).
---

## 7. KR ETF 이식 — 실행 결과 (2026-07-28)

구현 `experiments/causal_v2/{panel,decomp}.py` · 산출 `cache/decomp_kr.jsonl`
테스트월 2026-06·07 · arm=`ind` · $N$=252 · 게이트 $2\sigma$

### K1~K5 해소

| # | 결과 |
|---|---|
| K1 시장 | KOSPI200.KS 종가수익률 사용 |
| K2 우선주 | `is_primary_share_class` 로 판정 — ETF 보유 풀에서 **38종 검출**. NASDAQ 0종과 달리 KR 엔 실재. EXP-002 경고가 맞았다 |
| K3 exact-3 충족률 | **91.0%** (754/829 보통주). 실행 중 실측 **93.9%** 가 동일 industry 안에서 충족 |
| K5 업종분류 | `fmp_kr_stock_industry_map_20260619_172627.csv` — sector 11 / **industry 115** (중앙 5종목) · 커버 **99.5%** |

### 결과 — US 종목과 대조

| | US 종목 (EXP-002) | KR ETF |
|---|---:|---:|
| $R^2_{market}$ | 18.2% | **68.8%** |
| $\Delta R^2_{peer\perp}$ | +11.5%p | **+23.2%p** |
| 결합 | 29.6% | **92.0%** |
| market / thema / conc | 95.6 / 1.3 / 3.1 | **71.8 / 11.6 / 16.6** |

표본 1,632 ETF-일 · 51 ETF · 32 거래일.
피어 커버: 구성종목 92.4% · 비중 85.1%.

$R^2$ 상승은 ETF 가 분산 바스켓이라 구조적으로 예상되는 것이다.

### 데이터 정합성 검증 (3소스 교차)

| | |
|---|---|
| 지수 vs 시총가중 패널 | **상관 0.9816** · σ 2.68% vs 2.96% — 정합 |
| 지수 vs KODEX200(ETF) | σ 2.68% vs 2.64% · 일별 값 근접 — 정합 |
| 중앙값 패널과의 불일치(0.687)는 | 시총가중 대 중앙값 차이일 뿐. 데이터 결함 아님 |
| `changePercent` 정의 | **일중** $(close-open)/open$. 종가대비 아님 — 쓰지 마라 |

### 결함 — 숨기지 않음

| | |
|---|---|
| **$2\sigma$ 게이트 미교정** | 표본외 $\lvert r_1\rvert$ 가 창내 $\sigma_1$ 의 **1.49배**($r_2$ 는 1.30배). 정규 하 $\lvert z\rvert>2$ 기대 4.6% 인데 실측 **28.2%**. 라우트 경계는 이 창에서 명목 2σ 가 아니다 |
| 보유 스냅샷 단일 | `asof=2026-07-11` 을 6월 일자에도 적용 — **look-ahead 잔존** |
| 시간해상도 | ETF 5분봉 375중 2개 → **일간이 상한** |
| 동시점 attribution | EXP-002 K4 그대로 — ex-ante 예측력 아님 |

`concentrated` 는 날짜에 몰려 있지 않다 (일자별 비율 중앙 15.7% · 상위 3일이 전체의 19.6%,
균등 기대 9.4%). 변동성 국면 전환의 일괄 오분류는 **아니다**.

### 실험 대상 확정

| | |
|---|---|
| **`concentrated` 271 ETF-일** | 인과귀속 질문이 성립하는 층 |
| 사건 연결 | **271/271 = 100%** 가 PIT 창에 사건 보유 |
| 셀당 사건 | 중앙 **1,062건** · 평균 1,335 · 최대 4,449 |
| 사건 붙은 구성종목 비중합 | 중앙 81.3% |

**사건 밀도는 라우트를 구분하지 못한다** (중앙: market 941 · thema 272 · concentrated 1,062).
따라서 *"사건이 많으면 concentrated"* 라는 자명한 기준선이 존재하지 않는다 — 1회전에서
`event_count` 가 모든 것을 이겼던 문제(B1)가 여기서는 재발하지 않는다.

과제의 실제 모양: **수천 건 중 어느 구성종목의 어느 사건이 ETF 를 움직였는가.**
