# Architecture Decision Records (ADR)

중요한 설계·운영 결정을 한 형식으로 기록한다. 회의록이나 PRD가 아니라, **결정과 그 이유**만 증류한다.

- 형식은 [0000-template.md](0000-template.md) 하나로 고정한다 — 맥락 · 결정 · 대안 · 결과.
- 한 번 승인된 ADR은 수정하지 않는다. 결정이 바뀌면 **새 ADR을 추가하고** 옛 ADR의 상태를 `대체됨(→ ADR-XXXX)`으로 표시한다.
- 번호는 순차 증가. 파일명은 `NNNN-kebab-제목.md`.

| ADR | 제목 | 상태 |
|---|---|---|
| [0001](0001-monorepo-structure.md) | 모노레포·폴리글랏 워크스페이스 구조 | 승인됨 |
| [0002](0002-agent-instructions-ssot.md) | 에이전트 지침은 AGENTS.md 단일 출처 | 승인됨 |
| [0003](0003-branch-strategy.md) | 브랜치 전략 — dev 경유 엄격한 사다리 | 승인됨 |
| [0004](0004-squash-only-merge.md) | Squash 전용 머지 | 승인됨 |
| [0005](0005-db-as-contract.md) | DB를 단일 계약으로 · 확장-수축 마이그레이션 | 승인됨 |
| [0006](0006-gateway-single-edge.md) | gateway 단일 엣지 · 라우트별 신뢰 필터 | 승인됨 |
