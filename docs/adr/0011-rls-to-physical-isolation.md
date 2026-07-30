# ADR-0011: RLS 멀티테넌시에서 테넌트별 물리 격리로 전환

- 상태: 승인됨
- 날짜: 2026-07-12

## 맥락
피벗 전에는 단일 스키마 PostgreSQL에 RLS(Row-Level Security)로 테넌트를 격리하는 멀티테넌시 설계였고, 이를 위한 shared-tenancy 모듈을 두고 있었다. 하이브리드 피벗([ADR-0010](0010-hybrid-onprem-pivot.md))으로 고객 데이터가 전부 증권사 On-Premise에만 저장되면서, 클라우드 단일 DB 안에서 테넌트를 행 단위로 격리할 대상 자체가 사라졌다.

## 결정
**shared-tenancy(RLS) 모듈 삭제.** On-Prem이 테넌트별 물리 격리이므로 RLS의 존재 이유가 소멸. 고객 데이터는 On-Prem에만 저장한다(물리 격리). "RLS 설계 → 물리 격리 전환" 의사결정 자체는 기술 스토리로 문서화해 보존한다 — 이 ADR이 그 기록이다.

## 대안
클라우드 측 RLS 유지 — 클라우드에 잔존하는 테넌트 스코프 데이터(outbox 전달 레코드, 동기화 상태)는 전부 비개인화 데이터라 격리 실패 시 피해 반경이 동기화 메타데이터에 한정되므로, DB 계층 격리(RLS) 유지 비용 대비 효익이 낮다고 판단했다.

## 결과
- RLS 폐기 후 클라우드 측 테넌트 격리는 **Tenant Sync API의 매 요청 인증서-테넌트 바인딩 인가 검증**에서 강제된다 ([../contracts/sync-auth.md](../contracts/sync-auth.md)). 단일 계층(앱 계층) 방어인 점은 인지된 트레이드오프다.
- 방어 심화가 필요해지면 테넌트 스코프 테이블에 경량 tenant_id 스코핑(쿼리 강제 조건)을 재도입하는 것을 로드맵 옵션으로 남긴다.
- 구 RLS 설계가 담겼던 docs/schema.md는 삭제됐다 (`git log --follow`로 열람 가능). 테이블 단일 쓰기 소유자·확장-수축 절차([ADR-0005](0005-db-as-contract.md))는 피벗과 무관하게 유지 — [../implementation.md](../implementation.md)로 이동.
