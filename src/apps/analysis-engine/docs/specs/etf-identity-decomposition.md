---
doc_type: design
status: Draft
owner: engineering
created: 2026-07-10
updated: 2026-07-11
related:
  - ../baseline/analysis-engine-design.md
  - ../baseline/analysis-engine-design.md
  - ../../product/requirements/price-explanation.md
  - price-decomposition-engine.md
  - ../../research/price-decomposition/decisions/RDR-001-factor-selection.md
---
# ETF 항등식 분해 엔진

## Summary

이 문서는 ETF 설명 파이프라인의 앞단인 **R(타입 라우터) → L0(이상 게이트) → L1(항등식 분해) → 라우팅**의 계약을 소유한다(표기는 레이어 번호 순이며, 실행은 L1이 매일 선행한다 — 처리 흐름 절 참조). 다음 세 계약을 고정한다.

- ETF의 1차 분해는 회귀가 아니라 **항등식**이다: `가격수익률 = NAV수익률 + Δ괴리`, `NAV수익률 ≈ Σ(비중 × 구성종목수익률) + 환율분`.
- L0 진입 게이트는 v1에서 **절대 이동(`|가격수익률| ≥ 3%`)**과 **시장 대비 상대 이동** 두 트리거의 OR다. 보유비중·방향 가중과 z-score는 쓰지 않으며, 구성종목 상쇄(기여 게이트)·기대 불일치 트리거는 v1 범위 밖(defer)이다.
- 라우팅 통계(기여 집중도, breadth, 괴리 기여율)가 하류 진입 경로를 결정하고, 그 결정은 `explanation_route`로 감사 가능해야 한다.

L1은 게이트 결과와 무관하게 매일 실행된다. 항등식 산출물은 심층 분석의 진입 판단 재료이자, 정상 변동일의 가격 중심 설명 재료다.

## Context

- 레이어 구조와 설계 근거는 [시스템 아키텍처](../baseline/analysis-engine-design.md)가 소유한다. 본 문서는 R·L0·L1·라우팅의 데이터 요구사항과 단계 계약만 고정한다.
- 구성종목 요인 분해(L2)는 [가격 분해 엔진](price-decomposition-engine.md)이 소유한다. event-price 검증(P5–P7)은 **2026-07-20 재설계로 폐기**(방향 불검증 — [아키텍처 베이스라인](../baseline/analysis-engine-design.md) §10 폐기 그룹 참조).
- 데이터 이름은 요구사항이다([문서 작성 규칙](../../README.md)의 데이터 계약 원칙). 아래 이름은 "이런 grain의 데이터가 존재해야 한다"는 logical 계약이며, 물리 DB·스키마·테이블의 존재나 배치를 선언하지 않는다.

## Problem

ETF 레벨 회귀 분해의 문제(정보 손실, 피어 순환성, 상쇄 실명, 사후 보도 누수)는 아키텍처 문서 Problem 절이 소유한다. 본 문서가 답하는 질문은 하나다: **항등식 분해가 성립하려면 정확히 어떤 데이터가 요구되고, 각 단계의 입출력 계약은 무엇인가.**

## Goals

- R·L0·L1·라우팅 각 단계의 읽기/쓰기 계약을 필드 의미 수준에서 고정한다.
- 항등식이 어긋났을 때(reconciliation 오차)의 처리와 기록 방식을 고정한다.
- 데이터 미확보 시의 강등(fallback) 경로를 고정한다.

## Non-goals

- 요인 추정(L2), 이벤트 온톨로지, A–G 해석 (각 소유 문서 참조).
- 상대 게이트 `τ_rel`·라우팅 임계값 수치 확정 (연구 소유 — `research/price-decomposition/`; 절대 3%는 제품 결정으로 고정).
- 물리 스키마, 컬럼 타입, 인덱스 (계약 문서 소유).
- 장중 실시간(iNAV) 분해 (확장 후보로 유보).

## Raw input requirements

downstream 처리 전에 준비되어야 하는 raw input requirement다.

### `etf_reference` — ETF 참조 원장

grain: `(market, etf_ticker)`

| 필드 | 의미 |
|---|---|
| `market`, `etf_ticker` | natural key. KR ticker는 zero-padded TEXT |
| `etf_type` | 분해 템플릿 분기: 국내 테마·섹터 / 해외지수 / 레버리지·인버스 / 채권·금리 |
| `underlying_index_id` | 해외지수·레버리지형의 기초지수 식별자. 국내 테마형은 NULL 허용 |
| `leverage_multiple` | 배수. 비레버리지형은 1 |
| `fx_hedged` | 환헤지 여부 — L1에서 환율분 포함/제외를 가른다 |
| `available_at`, `data_version` | PIT 재현 metadata |

### `etf_holdings_snapshot` — 구성종목 원장

grain: `(market, etf_ticker, trade_date, constituent_ticker)`

| 필드 | 의미 |
|---|---|
| `market`, `etf_ticker`, `trade_date`, `constituent_ticker` | natural key |
| `weight` | NAV 대비 비중. `Σweight ≈ 1` 검사가 reconciliation의 첫 체크 |
| `basis_date` | 비중 산출 기준일. `trade_date`와 어긋날 수 있으며(T-1 PDF 관행) 항등식 오차의 1차 원인 |
| `available_at`, `data_version` | PIT 재현 metadata. 장전/장후 어느 시점에 사용 가능했는지가 재현성 경계 |

### `etf_nav_daily` — NAV 원장

grain: `(market, etf_ticker, trade_date)`

| 필드 | 의미 |
|---|---|
| `market`, `etf_ticker`, `trade_date` | natural key |
| `nav` | 좌당 순자산가치. `Δ괴리 = 가격수익률 − NAV수익률` 계산 근거 |
| `available_at`, `data_version` | PIT 재현 metadata |

가격 입력(`price_intraday`/`price_daily`)은 [가격 분해 엔진](price-decomposition-engine.md) P0 계약을 그대로 재사용한다. ETF 자신과 구성종목 모두 같은 계약으로 읽는다.

## Stage contracts

### R. 타입 라우터

| 읽기 | 쓰기 | 판단 |
|---|---|---|
| `etf_reference` | `explanation_route.template` | ETF 유형 → 분해 템플릿 선택 |

- 유형별 지배 분해와 이벤트 유니버스는 아키텍처 문서의 타입 라우터 표가 소유한다.
- `etf_type` 미확정·신규 상장은 `UNKNOWN` 템플릿으로 두고 review로 보낸다. 추정으로 채우지 않는다.

### L0. 이상 게이트

| 읽기 | 쓰기 | 판단 |
|---|---|---|
| ETF `price_daily`, 시장 벤치마크 `price_daily` | `etf_contribution_observation`의 `abs_gate`·`benchmark_id`·`rel_return`·`rel_gate`·`l0_entry` | 진입 여부(두 트리거 OR) |

v1 진입 트리거는 두 개이며 OR로 묶는다. 보유비중·방향 가중·z-score는 쓰지 않는다.

- **T1 절대 이동**: `abs_gate = |price_return| ≥ 0.03`. 임계 3%는 고정(제품 결정), 방향 무관(급등·급락 대칭).
- **T2 상대 이동**: `rel_return = price_return − benchmark_return`, `rel_gate = |rel_return| ≥ τ_rel`. 벤치마크는 시장 지수(해외지수·레버리지형은 R 라우터가 지정한 기초지수)이며 **피어 ETF는 금지**(순환성). `τ_rel`은 연구 소유이고 3%와 별개로(더 작게) 잡는다.
- `l0_entry = abs_gate OR rel_gate`. 미발화면 `normal_range`로 종료하고, 발화면 라우팅으로 넘긴다.
- L0는 실행 차단기가 아니라 진입 라벨이다. 진입 후 경로 판정은 라우팅 단계가 L1 통계로 내린다.
- **defer(v1 범위 밖)**: 구성종목 상쇄를 잡는 기여 게이트(req 6), 기대 불일치 트리거. 근거는 Open questions 6.

### L1. 항등식 분해

| 읽기 | 쓰기 | 판단 |
|---|---|---|
| `etf_holdings_snapshot`, `etf_nav_daily`, ETF·구성종목 가격, `etf_reference.fx_hedged` | `etf_contribution_observation`, `etf_contribution_member` | 기여·괴리·환율 분해와 reconciliation |

변환 계약:

- `Δ괴리 = 가격수익률 − NAV수익률` — 두 관측치의 차이므로 오차가 없다.
- 종목 기여 `c_i = weight_i × 종목수익률_i`, 환율분은 미헤지 해외형에만 계산한다.
- `recon_error = NAV수익률 − (Σ c_i + 환율분)` — 항등식 재구성 오차. 임계 초과 시 L1 신뢰도를 강등하고 원인 후보(`basis_date` 어긋남, 분배락, 보수)를 기록한다. 오차를 임의 항목에 흡수시키지 않는다.
- 기여 통계: 종목 변동성으로 정규화한 `contribution_z`를 계산해 라우팅(`concentrated`)에 쓴다. v1에서는 진입 트리거가 아니라 라우팅 입력이며, 상쇄 진입 게이트로의 승격은 defer(Open questions 6).

출력 필드 의미:

| 산출물 | grain | 핵심 필드와 의미 |
|---|---|---|
| `etf_contribution_observation` | `(market, etf_ticker, trade_date)` | `price_return`, `nav_return`, `premium_change`(=Δ괴리), `fx_return`, `recon_error`, L0 게이트(`abs_gate`, `benchmark_id`, `rel_return`, `rel_gate`, `l0_entry`), 라우팅 통계(`top_k_share`, `hhi`, `breadth_up_ratio`, `cross_dispersion`), `asof`, `data_version` |
| `etf_contribution_member` | `(market, etf_ticker, trade_date, constituent_ticker)` | `weight`, `constituent_return`, `contribution`(=c_i), `contribution_z`(변동성 정규화), `gate_flag` |

### 라우팅

| 읽기 | 쓰기 | 판단 |
|---|---|---|
| `etf_contribution_observation`(+`_member`), `constituent_decomposition_observation`(L2 산출 — 가격 분해 엔진 소유) | `explanation_route` | 잔차 z-score 캐스케이드 → route·scope 확정. 여기서 컨2 종료 |

판정은 **잔차 z-score 캐스케이드**다(2026-07-20 확정) — 이전 n일 회귀에서 β와 단계별 잔차 표준편차 σ를 얻고, "남은 잔차가 아직 특이한가"를 같은 임계 k로 반복해 묻는다:

- `r0` = 가격수익률, `r1 = r0 − β_m·시장수익률`, `r2 = r1 − β_p⊥·피어수익률⊥` — 피어는 시장에 직교화(테마 코호트 정의는 [아키텍처 베이스라인](../baseline/analysis-engine-design.md) §13 미결)
- `z_i = |r_i| / σ_i` — σ_i는 추정창에서 그 단계 잔차의 표준편차

판정 규칙(우선순위 순):

1. `l0_entry` 미발화 → `normal_range` — "특이 요인 없음"이 완결 출력. 하류 미실행.
2. holdings/NAV 결손 또는 추정 이력 부족(n일 미만) → `fallback_2leg` — 0 대체·추정 보충 금지.
3. `|Δ괴리|` 기여율 지배 → `flow_dominated` — 수급·유동성 설명, 이벤트 패스 없음.
4. `z1 < k` → `market_explained` — 가격·시장 요인 설명으로 종결, 이벤트 패스 없음.
5. `z2 < k` → `theme_comove` — scope=테마.
6. 그 외(`r2` 특이) → `concentrated` — scope=기여 상위 3 종목(`etf_contribution_member` 기여 기준). 집중도 통계(top-k share·`contribution_z`)는 판정 입력이 아니라 근거 기록이다.

파라미터: **k = 2 — v0 제품 결정으로 고정**(이벤트 스터디 5% 양측 관례 t≈1.96의 반올림; 절대 3% 게이트와 같은 지위). 조정은 [route 정답 라벨](route-ground-truth.md) 혼동행렬 개선을 증거로 `policy_version` 증가로만 한다. n(추정창)은 연구 소유(Open questions 1).

추정 규율:

- 베타·σ 추정창에서 과거 게이트 발화일은 제외한다 — 지난 이벤트가 오늘 판정의 기준선을 오염시키지 않게 (이벤트 스터디 관행).
- 피어 바스켓에서 자기 ETF·중복 구성종목을 제거한다.
- 벤치마크 자기중복은 **s-조건부로** 제거한다. 개별 원인이 시장 leg로 새는 유출률 ≈ `β·s/h`(`s` = 종목의 지수 내 비중, `h` = ETF 내 비중). 자기 구성종목의 지수 비중 합이 임계(연구 소유) 이상인 유형(반도체·대형 배터리 등)은 **자기 구성종목 제외 지수** `r_m_ex = (r_m − Σ w_i·r_i) / (1 − Σ w_i)`를 쓴다 — 필요 데이터는 자기 top 종목 몇 개의 지수 비중뿐(시총 근사 허용). 그 외 소형 테마는 plain 지수 허용. L0 T2 게이트는 라우팅과 동일 벤치마크를 쓴다.
- 직교화 절차: 같은 창·같은 제외 규칙으로 보조 회귀 `r_p = a + γ·r_m_ex + u`를 추정하고, 창과 당일 모두 `r_p⊥ = r_p − (a + γ·r_m_ex)`로 잔차화한다. β·γ·σ 추정은 전일까지(`T−n..T−1`), 당일 `T`는 적용만 한다. 절편은 모든 회귀에 포함(market model 관례).
- 퇴화 가드: 직교 후 `var(r_p⊥)`가 바닥이면(테마≈시장 잔존) 테마 단계를 건너뛰고 `r2 := r1`로 둔다 — 소음 분모로 가짜 `z2`를 만들지 않는다. 바닥 임계는 연구 소유.
- 라우터는 이벤트를 조회·판정하지 않으며, 이벤트의 가격 방향·타이밍 적합성을 계산하지 않는다([아키텍처 베이스라인](../baseline/analysis-engine-design.md) AE-R6–R8·§9 방향 불검증). 구모델 `common_factor`는 `market_explained`/`theme_comove`로 분화되었다(2026-07-20).

`explanation_route` grain: `(market, etf_ticker, trade_date)`. 핵심 필드: `template`, `route`, `scope_targets[]`, `z1`·`z2`·판정 근거 통계, `policy_version`, `asof`.

## 처리 흐름

이 엔진은 **매일 장 마감 후, ETF 한 종목에 대해 한 번** 실행된다. 하는 일은 "오늘 이 ETF가 왜 움직였나"의 **앞단**이다 — 무엇이 움직임을 만들었는지 오차 없이 **관측**하고, 그다음 설명을 어느 방향으로 파야 하는지 **경로를 정하는 것**까지다. 실제 뉴스·이벤트 해석(L3)이나 최종 서술(L4)은 하지 않고 하류로 넘긴다.

처음 보는 사람 기준으로, 하루치 실행을 순서대로 풀면 다음과 같다. 예시로 **"2차전지 테마 ETF, 당일 종가 +3.2%"** 하루를 따라간다.

### 0단계 — 타입 확정 (R)

먼저 이 ETF가 어떤 종류인지 `etf_reference`에서 읽는다. 종류에 따라 "무엇으로 분해할지"가 다르기 때문이다(국내 테마형은 구성종목 합, 해외지수형은 전일 기초지수+환율, 레버리지형은 배수, 채권형은 금리). 예시 ETF는 **국내 테마형**이라 "구성종목 기여 합"으로 분해하는 템플릿을 고른다. 종류를 모르면 추정하지 말고 `UNKNOWN`으로 두고 사람 검토로 보낸다.

### 1단계 — 입력이 준비됐는지 확인

분해에 필요한 4가지가 다 있는지 본다: **ETF·구성종목 가격, NAV(순자산가치), 구성종목 비중, 시장 벤치마크**. 하나라도 없으면 억지로 채우지 않는다. 특히 구성종목·NAV가 없으면 이 엔진의 정밀 분해를 포기하고, 이전 방식(시장+피어 2-leg)으로 강등하며 "데이터 없음(gap)"을 결과에 명시한다. 0으로 메꾸는 것은 금지다(관측의 성격이 깨지기 때문).

### 2단계 — 항등식 분해 (L1): "무엇이 움직였나"를 오차 없이 계산

ETF는 속이 보이는 바구니다. 그래서 추정(회귀) 없이 **회계 항등식**으로 움직임을 쪼갤 수 있다. 두 개의 식이 전부다:

- `가격수익률 = NAV수익률 + Δ괴리` — ETF 가격이 순자산가치보다 더/덜 오른 부분(Δ괴리)을 분리한다. Δ괴리는 두 관측치의 차이라 **오차가 0**이다.
- `NAV수익률 ≈ Σ(구성종목 비중 × 구성종목수익률) + 환율분` — NAV 움직임을 종목별 기여 `c_i`의 합으로 쪼갠다. 환율분은 미헤지 해외형에만 붙는다.

예시로 계산하면: 가격 +3.2% 중 Δ괴리 +0.1%p, NAV분 +3.1%p. NAV분을 종목별로 보니 20개 중 17개가 올랐고, 상위 3개 종목이 전체 기여의 45%를 차지했다.

이때 두 가지를 함께 남긴다. ① **reconciliation 오차**(`recon_error` = NAV수익률 − 계산한 기여 합): 비중 기준일 어긋남·분배락·보수 때문에 안 맞으면 그 크기를 기록하고 신뢰도를 낮춘다. 절대 임의 항목에 흡수시키지 않는다. ② **라우팅 통계**: 기여가 소수 종목에 몰렸는지(`top_k_share`, `hhi`), 넓게 퍼졌는지(`breadth_up_ratio`, `cross_dispersion`), 괴리가 얼마나 기여했는지. 이 통계가 4단계 경로 판정의 근거다.

산출은 ETF 하루 1행(`etf_contribution_observation`)과 구성종목별 행(`etf_contribution_member`)이다.

### 3단계 — 오늘이 설명이 필요한 날인가 (L0 이상 게이트)

대부분의 날은 특별한 이유 없이 정상 범위에서 움직인다. 그런 날까지 뉴스를 뒤지면 낭비이자 오탐이다. 그래서 진입 게이트를 둔다. v1 트리거는 두 조건의 **OR**다: **절대 이동**(당일 수익률 절댓값 3% 이상, 방향 무관)과 **상대 이동**(시장 벤치마크 대비 편차). 예시 +3.2%는 절대 게이트를 넘으므로 **진입**한다. 둘 다 조용하면 진입하지 않는다.

### 4단계 — 어느 방향으로 설명을 팔지 정한다 (라우팅)

2단계 통계와 3단계 게이트에 L2(구성종목 요인 분해)를 더해, **잔차 z-score 캐스케이드**로 딱 하나의 경로를 고른다. 순서대로: 결손이면 `fallback_2leg`, 괴리 지배면 `flow_dominated`, 시장 제거 후 잔차가 평상 범위(`z1 < k`)면 `market_explained`, 테마⊥까지 제거해 평상 범위(`z2 < k`)면 `theme_comove`(scope=테마), 그래도 특이하면 `concentrated`(scope=기여 상위 3 종목). 미설명 처리는 라우터의 일이 아니다 — top3 scope에서 후보를 못 찾으면 설명 에이전트가 `유의하나 미설명`으로 종료한다(베이스라인 EE-R8).

예시(+3.2%, 상승 17/20, 상위3 기여 45%): 시장 제거 후 `z1`이 k를 넘으면 테마⊥를 제거한다 — `z2`가 k 아래로 내려오면 `theme_comove`를 기록한다. 판정과 `z1`·`z2`는 `explanation_route`에 남는다.

### 5단계 — 하류로 넘김 (handoff)

경로가 `normal_range`·`flow_dominated`·`market_explained`·`fallback_2leg`면 여기서 끝난다(이벤트 불필요). `concentrated`·`theme_comove`면 `explanation_route`(+`scope_targets[]`)와 기여 마트를 설명 에이전트(컨3)에 넘긴다. 이 엔진은 이벤트 해석을 직접 하지 않는다 — "어디를 봐야 하는지"까지만 정해서 넘긴다.

### 재현성

모든 단계는 `data_version`(입력 데이터 버전)과 `asof`(정보 기준 시각)로 고정된다. 같은 입력·버전·기준 시각이면 언제 다시 돌려도 같은 분해·같은 경로가 나와야 한다.

## 대안

| 대안 | 판단 |
|---|---|
| ETF 레벨 시장+피어 2-leg 유지 | 정보 손실·피어 순환성. 구성종목 데이터 미확보 ETF의 fallback 전용 |
| iNAV 기반 장중 실시간 분해 | 가치 있으나 데이터·운영 비용이 크다. 일간 분해 안정화 후 확장 |
| 괴리를 오차항으로 흡수 | Δ괴리는 관측치이며 수급 신호다. 오차항 취급 금지 |

## 위험과 실패 처리

- **구성종목·NAV 미확보/지연**: `route = fallback_2leg`로 기록하고 이전 리비전 2-leg 모델로 강등. 출력에 데이터 gap 명시, 0 대체 금지.
- **`basis_date` 어긋남·분배락**: `recon_error`로 드러난다. 임계 초과 시 신뢰도 강등과 원인 기록. 조용히 보정하지 않는다.
- **구성종목 가격 결측**(거래정지 등): 해당 기여는 `UNKNOWN`으로 남기고 reconciliation에서 제외 사실을 기록한다.
- **유형 오분류**: `UNKNOWN` 템플릿과 review 경로가 방어한다. 잘못된 템플릿의 조용한 실행이 최악이다.

## 검증 방법

- reconciliation: ETF·일 단위 `recon_error` 분포가 임계 내인지, 초과 케이스에 원인 필드가 채워지는지.
- 라우팅 재현성: 동일 입력·버전·`asof`에서 동일한 `route`.
- 상쇄 포착(defer 기능 검증): 진입일 중 총량은 작지만 구성종목 상쇄가 큰 케이스 비율. v1 2-트리거 게이트는 이 케이스를 놓치므로(req 6 미충족), 기여 게이트 승격 시 재평가한다(H-004~H-005 연결).
- fallback 커버리지: 강등 경로가 유니버스의 어느 비율을 차지하는지 추적.
- **route 정오 벤치**: 라우팅 판정을 [route 정답 라벨](route-ground-truth.md)(시장/테마/개별)과 대조한 혼동행렬로 검증한다. 라벨은 무라우터 전수 분석이 생산한다.

## Open questions

1. 상대 게이트 `τ_rel`·라우팅 임계값과 recall·비용 trade-off (연구 소유, H-003 계승) — 실측 계기판은 [route 정답 라벨](route-ground-truth.md).
2. `basis_date` 정책: T-1 PDF를 당일 비중으로 쓸 때의 허용 오차.
3. 해외지수형의 환헤지 비율 데이터 확보 경로.
4. 레버리지·인버스의 롤·복리 항 정밀도 요구 수준.
5. `etf_holdings_snapshot`·`etf_nav_daily`의 소스 선정과 계약 문서화 시점.
6. v1 진입 게이트(절대 3% OR 상대)는 구성종목 상쇄일(req 6)을 놓친다. 기여 게이트를 3번째 진입 트리거로 승격할 시점·임계값 — H-004~H-005 검증 후 결정. 기대 불일치 트리거의 승격 여부도 여기서 함께 트래킹한다(기대 모델은 market-expectation draft 소관).

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 상위 아키텍처 | [아키텍처 베이스라인](../baseline/analysis-engine-design.md) — 구 `docs/engineering/current-architecture.md`는 **유실** | 레이어 구조, 타입 라우터 표(재작성 대상), Problem 정의 |
| 요구사항 | `docs/product/requirements/price-explanation.md` — **유실**. 대역: 베이스라인 §4 SYS-R\* | Required behavior 3–6, 예외 상황의 강등·판정불가 |
| 가격 입력 계약 | `price-decomposition-engine.md` P0 | `price_intraday`/`price_daily` 재사용 |
| 식별자·시간 규칙 | `docs/operations/data/edge-db-ground-rules.md` | `market`, `ticker`, `trade_date` 규칙 |
