---
doc_type: proposal
status: Deferred
owner: engineering
created: 2026-07-10
updated: 2026-07-20
related:
  - ../baseline/analysis-engine-design.md
  - ../specs/data/disclosure-types.md
  - ../specs/data/entity-master.md
---
# 제안 0002 — 관계 그래프·영향 경로 (강등 · 현재 범위 밖)

> **상태: Deferred.** 관계 그래프와 영향 경로(구 Explanation D) 설계를 보관한다. 복잡성 감축을 위해 메인라인에서 들어냈고, 필요해질 때 승격한다.

## Context — 왜 강등했나

- 관계 그래프의 **유일한 런타임 소비는 영향 경로(D)의 2홉 순회 한 곳**뿐이었다.
- 실제 투영 relation은 공시 `produces`·`supplies` **2종**뿐이고, `owns/substitute/complement/input_of`는 allowlist만 있고 미사용.
- 비용(그래프 적재·유지·순회·2홉 캡) 대비 설명 기여가 얕아, 들어내면 파이프라인이 단순해진다.

## Design — 강등된 것 / 현재 대체

**강등된 것**

| 대상 | 원래 위치 |
|---|---|
| 관계 엣지 저장 `graph_edges`, `graph_edge_document_link` | 그래프 store |
| relation allowlist (`produces`/`supplies`/`owns`/…) | 그래프 store |
| 공시 관계 그래프 projection (filing → 엣지) | 공시 파이프라인 |
| 영향 경로(D): event → issuer·segment·theme → ETF 2홉 순회 | [analysis-engine-design.md](../baseline/analysis-engine-design.md) §8 |

**현재 대체 (단순화)** — 영향 경로를 **직접 membership 단일 홉**으로 대체:

- event 엔티티(issuer)가 ETF **구성종목**인가 → `etf_contribution_member` 조회.
- event가 **코호트/테마**인가 → 코호트 바스켓이 ETF 구성종목과 겹치는가.
- 겹치면 정합성 단계에서 상위 기여 종목인지 대조. **그래프 순회 없음.**

즉 "event가 ETF에 닿는 경로"를 그래프로 찾지 않고, "event 엔티티가 ETF 바스켓에 있는가"만 본다. **ETF→설명단위→event 2홉 캡** 강제.

## 보관되는 것 (강등 대상 아님)

- **개념/엔티티 노드 마스터** ([엔티티 마스터](../specs/data/entity-master.md)) — 엔티티 해소용 참조 데이터. 이 문서는 EDGE(관계)만 다룬다.
- 공시 **정규화 사실**(`supply_contract`, `business_segments`) — 이벤트 규모·중요도(E) 근거로 유지. 강등은 그 사실을 **관계 엣지로 투영**하는 부분만.

## Alternatives

간단히만: 관계 그래프를 유지하되 relation 종류를 2종으로 축소하는 안도 있었으나, 유지 비용 자체가 얇은 기여 대비 커서 전면 강등을 택했다.

## Restore — 복원 조건

다중 홉 관계가 실제 설명력을 줄 때 — 공급망 스필오버, 소유구조 전파, 테마 간접 연결 등이 **직접 membership으로 안 잡히는** 케이스가 누적되면 이 제안을 검증하고 승격한다.

## References

- 강등 전 D 원설계 요지: [analysis-engine-design.md](../baseline/analysis-engine-design.md) §8
- 공시 관계 타입: [disclosure-types.md](../specs/data/disclosure-types.md)
- 엔티티 노드 마스터: [entity-master.md](../specs/data/entity-master.md)
