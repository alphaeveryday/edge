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
| [0004](0004-squash-only-merge.md) | Squash 전용 머지 | 대체됨 (→ [0007](0007-merge-strategy.md)) |
| [0005](0005-db-as-contract.md) | DB를 단일 계약으로 · 확장-수축 마이그레이션 | 승인됨 |
| [0006](0006-gateway-single-edge.md) | gateway 단일 엣지 · 라우트별 신뢰 필터 | 대체됨 (→ [0010](0010-hybrid-onprem-pivot.md)) |
| [0007](0007-merge-strategy.md) | 머지 전략 — 경계별(feature→dev Squash, dev→main Merge commit) | 승인됨 |
| [0008](0008-super-admin-console.md) | super-admin 콘솔 — cross-tenant 운영자 표면 | 승인됨 |
| [0009](0009-aws-deployment-topology.md) | AWS 배포 토폴로지 — Terraform IaC · 단계 스택 | 제안됨 |
| [0010](0010-hybrid-onprem-pivot.md) | 하이브리드 On-Premise 피벗 — 고객 접점·컴플라이언스의 증권사 이전 | 승인됨 |
| [0011](0011-rls-to-physical-isolation.md) | RLS 멀티테넌시에서 테넌트별 물리 격리로 전환 | 승인됨 |
| [0012](0012-sync-cert-bootstrap.md) | Sync 인증서 부트스트랩 — CSR 방식, 개인키 비반출 | 승인됨 |
| [0013](0013-exposure-log-recording.md) | Exposure Log 기록 — 조회 시점 자동 기록, 고객 해시는 증권사 생성 | 승인됨 |
| [0014](0014-correction-repreview.md) | 정정 이벤트 처리 — 무조건 재검수 | 대체됨(0041) |
| [0015](0015-sync-protocol-mvp.md) | Sync 프로토콜 — cursor 기반 delta, MVP 스펙과 목표 계약 분리 | 대체됨 (→ [0040](0040-sync-integrity-mvp-to-signing.md)) |
| [0016](0016-single-repo-two-artifacts.md) | 코드베이스 — 단일 레포, 배포 아티팩트 2종 | 승인됨 |
| [0017](0017-demo-topology-compose.md) | 데모/개발 토폴로지 — 별도 EC2 + Docker Compose 가상 온프렘 | 승인됨 |
| [0018](0018-rule-deployment-path.md) | Compliance Rule 배포 경로 — Rule Type은 릴리스, Sync 채널은 데이터 전용 | 승인됨 |
| [0019](0019-team-ownership-interface.md) | 팀 오너십 — Sync 프로토콜 양단 단일 오너, 인터페이스 계약 고정 | 대체됨 (→ [0026](0026-ownership-boundary-db.md)) |
| [0020](0020-verification-realign.md) | 검증 반영 — 규제 서사 재정렬 및 설계 정합 일괄 정정 | 승인됨 |
| [0021](0021-design-reinforcement.md) | 설계 보강 — DMZ 배치·cursor 발번 시점·정정 리비전 모델 확정 | 승인됨 |
| [0022](0022-verification-round2.md) | 검증·보강 2차 — 규제 사실관계 외부 검증 및 배치·온보딩 옵션 보강 | 승인됨 |
| [0023](0023-customer-validation.md) | 고객 검증 반영 — 증권사 현업 리뷰 확보·이해상충은 노출 범위 제외로 통제 | 승인됨 |
| [0024](0024-scope-domestic-etf.md) | MVP 상품 범위 — 국내 ETF, 스키마는 미국 확장성 선반영 | 승인됨 |
| [0025](0025-onprem-auth-hybrid.md) | 온프렘 콘솔 인증 — SSO/AD 지향 + 데모 자체 계정 하이브리드 | 승인됨 |
| [0026](0026-ownership-boundary-db.md) | 팀 오너십 경계 정정 — 진기는 DB 적재까지, 인터페이스는 DB 스키마 | 승인됨 |
| [0027](0027-entity-id-scheme.md) | 도메인 ID 체계 — 불투명 서로게이트(ULID), 외부 식별자는 속성 | 승인됨 |
| [0028](0028-unified-pipeline-sfn.md) | 파이프라인 SFN 통합 — 4페이즈(raw→정제→feature→분석)와 feature/분석 경계 | 승인됨 |
| [0029](0029-apps-plane-grouping.md) | apps 플레인 그룹핑 · schema 마이그레이션 세트 대칭 명명 | 승인됨 |
| [0030](0030-raw-phase-partial-failure.md) | raw 페이즈는 부분 실패를 격리한다 — 전량 성공 게이트 제거 | 승인됨 |
| [0031](0031-serving-to-publication.md) | serving-api를 Publication 도메인으로 리네이밍 | 승인됨 |
| [0032](0032-retire-gateway.md) | gateway 은퇴 — 클라우드 엣지를 ALB 직결로 | 승인됨 |
| [0033](0033-demo-onprem-stack.md) | 데모 온프렘 terraform 스택 분리 — 실 클라우드와 state 격리 | 승인됨 |
| [0034](0034-host-per-edge-alb.md) | 공개 엣지 호스트 단위 분리 — 서비스당 ALB 1개, 경로 라우팅 없음 | 승인됨 |
| [0035](0035-widget-ui-build-artifact.md) | 위젯 UI를 빌드 산출물로 납품 — 실행 서버 없음 | 승인됨 |
| [0036](0036-sync-agent-intake-topology.md) | Sync 온프렘 토폴로지 — Sync Agent(DMZ)+Intake(내부망) 2모듈 표준 | 승인됨 |
| [0037](0037-compliance-engine-to-screening-worker.md) | 점검 실행 모듈 명칭 — Compliance Engine → Screening Worker | 승인됨 |
| [0038](0038-jpa-onprem-read-standard.md) | 온프렘 조회 표준으로 JPA 도입 — 스키마는 Flyway SSOT, 앱은 validate-only | 승인됨 |
| [0040](0040-sync-integrity-mvp-to-signing.md) | Sync 번들 무결성 — 체크섬·byte[] 응답을 MVP에서 목표 계약(서명)으로 이관 | 승인됨 |
| [0041](0041-correction-same-screening.md) | 정정 리비전도 신규와 동일한 정책 평가 — 0014 대체 | 승인됨 |
