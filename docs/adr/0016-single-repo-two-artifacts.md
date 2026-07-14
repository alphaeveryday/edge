# ADR-0016: 코드베이스 — 단일 레포, 배포 아티팩트 2종

- 상태: 승인됨
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #5 (2026-07-12)

## 맥락
하이브리드 피벗([ADR-0010](0010-hybrid-onprem-pivot.md))으로 배포 대상이 Vendor Cloud와 증권사 On-Premise 둘로 갈라졌다. 레포를 나눌지, 기존 Gradle 멀티모듈([ADR-0001](0001-monorepo-structure.md))을 어떻게 재편할지 정해야 했다.

## 결정
단일 레포, 배포 아티팩트 2종(edge-cloud/edge-onprem). shared-tenancy(RLS) 삭제.

- `edge-cloud` (super-admin, tenant-sync-api, pipeline 연동) / `edge-onprem` (sync-agent, compliance-engine, tenant-console, serving-api).
- 기존 Gradle 멀티모듈에서 widget 모듈 삭제, tenant-console은 onprem 아티팩트로 이동.
- Flyway 중앙화(shared-migration)는 유지하되 cloud/onprem 마이그레이션 세트를 분리.

상세는 [../implementation.md](../implementation.md).

## 대안
원문(컨텍스트 문서 v2.0)에 검토 대안이 별도로 기록되지 않았다. RLS 삭제의 판단 근거는 [ADR-0011](0011-rls-to-physical-isolation.md).

## 결과
- 모노레포 구조([ADR-0001](0001-monorepo-structure.md))는 유지되고, 산출물 경계만 아티팩트 2종으로 분리된다.
- widget 모듈 삭제·tenant-console 이동은 서비스/API 변경표([../context.md](../context.md))를 따른다.
