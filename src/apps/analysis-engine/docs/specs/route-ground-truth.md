---
doc_type: design
status: Draft
owner: engineering
created: 2026-07-20
updated: 2026-07-20
related:
  - ../baseline/analysis-engine-design.md
  - etf-identity-decomposition.md
  - data/thread-types.md
  - data/disclosure-types.md
---
# Route 정답 라벨 — 라우터 검증 벤치

## Summary

- 질문 하나만 라벨링한다: **"그날 가장 중요한 이슈는 시장·테마·개별종목 중 어디에 있었는가."**
- 방식: 라우터 없이 세 후보를 전부 검토하는 오프라인 전수 분석으로 `(market, etf_ticker, trade_date)`마다 라벨 1개를 확정한다.
- 용도: 라우터 판정(route)과의 혼동행렬. 잔차 임계 θ·베타 창 n·τ_rel 선정과 route 설계 변경은 이 라벨과의 대조로 판정한다 — **앞으로 설계의 기준선** (2026-07-20 결정).

## 라벨 계약 — `route_ground_truth`

grain: `(market, etf_ticker, trade_date)` 1행.

| 필드 | 뜻 |
|---|---|
| `true_cause` | `MARKET \| THEME \| IDIO` |
| `cause_targets[]` | THEME → 테마 식별자, IDIO → 종목. MARKET은 빈 배열 |
| `evidence_refs[]` | 근거 event·thread·공시 참조 — **필수**, 없으면 라벨 무효 |
| `labeler_version`, `asof` | 재현성 |

우세를 가릴 수 없는 날은 **라벨을 내지 않는다** — 억지 3자택일이 라벨 없는 날보다 나쁘다.

## 절차

1. 대상은 게이트 발화일. 라벨러는 scope 제한 없이 시장·테마·개별 후보를 모두 검토하고 가장 중요한 이슈 1개를 고른다.
2. 근거 참조 없는 라벨은 무효.
3. 라벨↔라우터 불일치 케이스만 사람이 검수한다.

## 지표

- **혼동행렬**: `true_cause` × route — `MARKET↔market_explained`, `THEME↔theme_comove`, `IDIO↔concentrated`. `policy_version`별로 산출.
- 과적합 방지: 임계 튜닝에 쓴 기간과 검증 기간을 분리한다.

## 지금 안 하는 것 (필요가 증명되면 승격)

수급/혼합/정상 라벨, 증거 등급(공시 앵커 우선), PIT-공정 지표, lockbox 운영 — v0 혼동행렬이 실제로 굴러간 뒤에.

## Open questions

1. 라벨 유니버스 — backfill 기간과 ETF 범위.
2. 라벨러 구현 — 현행 `view_loop.py` 경로 재사용 vs 라벨 전용 경량 에이전트.
