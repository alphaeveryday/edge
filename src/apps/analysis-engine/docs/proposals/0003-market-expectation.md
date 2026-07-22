---
doc_type: proposal
status: Deferred
owner: engineering
created: 2026-07-10
updated: 2026-07-20
related:
  - ../baseline/analysis-engine-design.md
  - ../specs/price-decomposition-engine.md
---
# 제안 0003 — 시장 기대·기대 대비 차이 (강등 · 현재 범위 밖)

> **상태: Deferred.** "시장 기대(consensus·priced-in) 정밀 추정"과 그에 기반한 **기대 대비 차이(surprise, 구 Explanation C)**는 정밀 소스가 없어 메인라인에서 제외하고 보관한다.

## Context — 왜 강등했나

"기대 대비 차이"는 오늘 이벤트가 **시장이 이미 기대하던 것 대비 얼마나 벗어났나(surprise)**를 재는 것이다. 그러려면 per-event **시장 기대치**가 필요한데 현재 정밀 소스가 없다:

- **analyst consensus/estimates 없음** — 실적·가이던스 컨센서스 피드 부재.
- **options-implied 기대 없음** — 내재변동성·스큐 기반 기대 분포 피드 부재.
- **priced-in baseline 정량화 수단 없음**.
- `response_prior`는 **과거 유사 event 평균 반응** 요약이지 per-event 시장 기대가 아니다 — 이를 기대치로 쓰면 historical prior와 market expectation을 혼동한다.

정밀 기대 없이 surprise를 계산하면 근거 없는 beat/miss 서사가 되므로 억지로 만들지 않고 강등한다.

## Design — 강등된 것 / 현재 대체

**강등된 것**: Explanation **C. 기대 대비 차이**, A의 "이미 시장이 알던 baseline" 부분, `C.surprise_assessment` 중간 산출.

**현재 대체 (available 근사)** — 시장 기대 대신 소스가 있는 근사만 쓴다:

- **novelty (A)** — `event_thread`/`thread_discovery_snapshot` 기반 신규/후속/재보도 판정. "시장에 이미 있던 정보인가"의 근사. 단 "consensus 대비 beat/miss"는 주장하지 않는다.
- **무결성 (E)** — 숫자·단계 완결성으로 "말할 가치가 있는 사실인가".
- **가격 정합성** — 이미 시장·테마 leg로 설명된 움직임을 event로 중복 서사화하지 않는다(surprise의 부분 대체).

## Alternatives

간단히만: `response_prior`를 기대치 대용으로 쓰는 안이 있으나 historical prior ≠ market expectation이라 기각. 정밀 소스 확보 전까지는 근사만 사용.

## Restore — 복원 조건

analyst consensus/estimates 피드 또는 options-implied 기대 소스를 확보하면, per-event 시장 기대치를 만들고 surprise(C)를 정량 단계로 복원한다.

## References

- 강등 전 C 원설계 요지: [analysis-engine-design.md](../baseline/analysis-engine-design.md) §8
- 가격 분해 계약: [price-decomposition-engine.md](../specs/price-decomposition-engine.md)
