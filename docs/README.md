# edge 문서 (docs/)

이 디렉토리는 시스템의 **설계 지식** — 제품 컨텍스트, 구조, 데이터 계약, 결정 기록 — 의 **단일 출처(SSOT)** 다.
여기 문서와 다른 문서·발표 자료가 충돌하면 이 디렉토리가 기준이다. **변경은 PR 리뷰 필수** — docs/를 리뷰 없는 직접 커밋으로 고치지 않는다.
운영 규칙(브랜치·커밋·머지)은 docs가 아니라 루트 [README.md](../README.md)·[AGENTS.md](../AGENTS.md)에 둔다.

> **적용 시점**: 2026-07 아키텍처 피벗 이후 ([adr/0010](adr/0010-hybrid-onprem-pivot.md)). 이 디렉토리와 충돌하는 기존 문서(Cloud-only / embed widget 구조)는 전부 폐기된 방향이다.

## 문서 지도

| 문서 | 무엇을 담나 | 언제 보나 |
|---|---|---|
| [context.md](context.md) | 제품 정의·변경 배경, 기존 vs 신규, 하이브리드 아키텍처 개요, Cloud/On-Prem 책임 분리, 서비스/API 변경표 | 전체 그림을 잡을 때 (필독) |
| [scope.md](scope.md) | MVP 제외 범위 (명시적 아웃) | 기능 범위가 헷갈릴 때 |
| [implementation.md](implementation.md) | 구현 결정 현행 스펙 — 코드베이스, 데모 토폴로지, Rule 배포 경로, DB 변경 절차, 현행 CD | 구현·배포·스키마를 바꾸기 전 |
| [roadmap.md](roadmap.md) | MVP 이후 확장 가능 영역 | "이건 지금인가 나중인가" 판단 시 |
| [writing-rules.md](writing-rules.md) | 문서 작성 톤 규칙 (모든 산출물 공통) | 문서·UI 문구·발표 자료 작성 시 |
| [contracts/sync-protocol.md](contracts/sync-protocol.md) | Sync 프로토콜 계약 (cursor·fan-out·멱등성·목표 계약) — 양단 모두 영서 소유 ([adr/0026](adr/0026-ownership-boundary-db.md)) | Sync 채널 양단을 만질 때 |
| [contracts/event-bundle-schema.md](contracts/event-bundle-schema.md) | 진기-영서 인터페이스 계약 (Cloud Event Store 스키마 경계면) + 번들 와이어 포맷(영서 소유) — 스키마 경계면은 **공동 승인(CODEOWNERS)** | 인터페이스 경계를 바꿀 때 |
| [contracts/sync-auth.md](contracts/sync-auth.md) | 인증서 / Cloud Sync 인증 정책 (mTLS·CSR·교체) | Sync 인증을 만질 때 |
| [contracts/publication-api.md](contracts/publication-api.md) | MTS/HTS 연동 방식 — Publication API | 증권사 연동 접점을 만질 때 |
| [contracts/console-facts-api.md](contracts/console-facts-api.md) | Super Admin Console facts API — 규칙 엔진이 읽는 사실 계약 ([adr/0050](adr/0050-console-facts-endpoint.md)) | 콘솔 규칙 엔진의 입력을 만질 때 |
| [domain/state-machine.md](domain/state-machine.md) | 데이터 플로우, 정정/무효화 플로우, ERD 방향·상태값·리비전 모델 | 상태·전이·검수 로직을 만들 때 (필독) |
| [domain/data-residency.md](domain/data-residency.md) | 데이터 저장 위치 기준 (Cloud 가능/금지, On-Prem 필수) | 데이터를 어디에 저장할지 정할 때 |
| [domain/exposure-log.md](domain/exposure-log.md) | Exposure Log / 고객 식별 | 노출 이력·감사 재현을 만질 때 |
| [domain/data-source-licensing.md](domain/data-source-licensing.md) | 외부 데이터 소스 재제공 리스크 스냅샷 (참고용·비블로커, best-effort) | 실증권사 납품/실사 대비·데이터 소스 라이선스가 궁금할 때 |
| [console-ia/](console-ia/) | Super Admin·Tenant Console IA | 콘솔 화면·메뉴를 만들 때 |
| [adr/](adr/) | 결정 기록 — 무엇을 왜 그렇게 정했나 | 결정의 배경이 궁금할 때 |
| [architecture/system-architecture.md](architecture/system-architecture.md) | **[뷰]** 논리 컴포넌트↔시스템 매핑 + 논리→배포 모듈 매핑표 | 논리 구조를 훑을 때 (상세: context.md) |
| [architecture/application-architecture.md](architecture/application-architecture.md) | **[뷰]** 환경별 UI/API/저장소 계층 | 앱 계층을 훑을 때 (상세: context.md·console-ia) |
| [architecture/information-architecture.md](architecture/information-architecture.md) | **[뷰]** 콘솔 정보구조 트리(위젯·고객사·슈퍼어드민) | 콘솔 IA를 훑을 때 (상세: console-ia) |
| [architecture/cloud-architecture.md](architecture/cloud-architecture.md) | **[뷰]** AWS 클라우드 인프라 구성도 | 클라우드 배치를 훑을 때 (상세: infra/terraform/README·adr/0034·0028) |
| [analysis-engine/ontology/ontology-system-spec.md](analysis-engine/ontology/ontology-system-spec.md) | 온톨로지 4층·선언 규칙·로더 게이트·DB 사상 SSOT | 온톨로지 리소스·로더·소비자를 변경할 때 |
| [design/data-source-unification-spec.md](design/data-source-unification-spec.md) | 분석 소비 데이터 표면 전수표·존 통일 원칙·백필→포워드 전환 목록·수집 파이프라인 스펙 (ALPHA-879) | 데이터 소스를 새로 상시화하거나 수작업 백필을 정리할 때 |
| [design/open-source-backfill.md](design/open-source-backfill.md) | 오픈소스 백필 수집 방법 기록 (2026-08-02 일회성 — 적재 규약·벤더 함정) | 그 백필 데이터의 출처·함정이 궁금할 때 |

> **뷰 vs SSOT**: `architecture/`는 `EDGE_아키텍처_v0_2.pptx` 슬라이드에서 옮긴 **설계 뷰(논리 개요)** 다. 현행 사실·계약의 권위는 위 SSOT 문서(context.md·console-ia·contracts·domain·adr·infra README)에 있고, 뷰는 그 상세로 링크한다. **충돌 시 SSOT 우선.** 뷰가 SSOT보다 앞선 축(설계 의도)은 조용히 뷰를 따르지 말고 ADR/context 결정으로 SSOT를 전진시킨다. (2026-07-13 삭제된 구 `docs/architecture.md`와는 무관 — 아래 이관 기록 참조.)

## 읽는 순서
1. [context.md](context.md) — 제품이 무엇이고 왜 피벗했는지, 컴포넌트 경계
2. [scope.md](scope.md) — 무엇을 만들지 않는지
3. [domain/state-machine.md](domain/state-machine.md) — 콘텐츠 생애주기
4. [contracts/](contracts/) — 경계 계약 (필요할 때)
5. [adr/](adr/) — 개별 결정의 맥락 (필요할 때)

## 팀 오너십

- **김진기**: Data Pipeline → Common Analysis Engine → **Cloud Event Store 적재까지** (DB에 쓰는 것까지 — [adr/0026](adr/0026-ownership-boundary-db.md)).
- **조영서**: DB를 소비하는 **이후 전부** — Event Bundle 생성(tenant-sync-api), 전달 레코드(fan-out), Sync Agent·Intake, Screening Worker, Tenant Console (API), Publication API, Super Admin Console API. Sync 프로토콜 양단을 단일 오너가 설계.
- **정준영**: AI/ML — 설명 후보 생성, 신뢰도/반대 요인 산출.
- 진기-영서 인터페이스는 **Cloud Event Store DB 스키마** 하나로 고정한다(db-as-contract). 스키마 변경은 반드시 양자 합의 ([contracts/event-bundle-schema.md](contracts/event-bundle-schema.md), [adr/0026](adr/0026-ownership-boundary-db.md)).

> **백엔드 커리어 축 업데이트**: 기존 4축 중 RLS 멀티테넌시(→ "RLS에서 물리 격리로의 전환 의사결정" 스토리로 전환)와 API Key 라이프사이클(제거)이 약화되고, 신규 축으로 **① cursor 기반 delta sync 프로토콜 설계(멱등성/순서보장/정정 전파) ② mTLS 인증서 라이프사이클(CSR/CA/교체)** 이 공식화됨. Audit 무결성, Outbox/재시도 축은 유지.

## 무엇을 어디에 두나
- 설계·결정·계약 → `docs/` (새 문서는 위 지도에 등록).
- 운영 규칙(브랜치/커밋/머지) → 루트 README / AGENTS가 SSOT.
- 결정은 ADR로 증류한다 — 회의록·PRD 원본은 레포에 두지 않는다.

## 원문 이관 기록 (2026-07-13)

컨텍스트 문서 v2.0(`docs/_source.md`, 이관 후 삭제)의 섹션별 이관 위치. 누락 0건 확인 후 원문을 삭제했다.

| 원문 섹션 | 이관 위치 |
|---|---|
| 문서 머리(목적·적용 시점·작성 기준) | context.md |
| 1 제품 정의 및 변경 배경 | context.md |
| 2 기존 구조 vs 신규 구조 | context.md |
| 3 하이브리드 아키텍처 개요 (다이어그램 Mermaid 변환) | context.md |
| 4 Cloud/On-Premise 책임 분리 | context.md |
| 5 서비스/API 변경표 | context.md |
| 6 데이터 플로우 | domain/state-machine.md |
| 7 컴플라이언스 플로우 — 무효화 처리 | domain/state-machine.md |
| 8 Sync 프로토콜 계약 | contracts/sync-protocol.md |
| 9 인증서/Cloud Sync 인증 정책 | contracts/sync-auth.md |
| 10 Exposure Log/고객 식별 | domain/exposure-log.md |
| 11 Super Admin Console IA | console-ia/super-admin-console.md |
| 12 Tenant Console IA | console-ia/tenant-console.md |
| 13 데이터 저장 위치 기준 | domain/data-residency.md |
| 14 MTS/HTS 연동 방식 | contracts/publication-api.md |
| 15 ERD 방향 및 상태값 | domain/state-machine.md |
| 16 MVP 제외 범위 | scope.md |
| 17.1~17.3 구현 결정사항 | implementation.md |
| 17.4 팀 오너십 | README.md(이 문서) "팀 오너십" |
| 18 향후 확장 가능 영역 | roadmap.md |
| 19 문서 작성 톤 규칙 | writing-rules.md |
| 20 확정 결정 로그 11건 | adr/0012~0022 (결정 로그 #1~#11 순) |
| 20 말미 백엔드 커리어 축 노트 | README.md(이 문서) "팀 오너십" 하단 |

후속 이관 (2026-07-15): 원문 결정 로그 #12(2026-07-14, 고객 검증 반영 — 이관일 이후 노션 추가분) → [adr/0023](adr/0023-customer-validation.md) + [console-ia/tenant-console.md](console-ia/tenant-console.md) 이해상충 항목.

같은 PR에서 구(舊) 구조 문서를 삭제하고 유효분을 흡수했다:
- `docs/architecture.md` (embed widget/클라우드 단일 구조) → 삭제. 구 구조 요지는 [adr/0010](adr/0010-hybrid-onprem-pivot.md) 맥락, 현행 CD 서술은 [implementation.md](implementation.md) §5로 흡수.
- `docs/schema.md` → 삭제. 소유권 원칙·확장-수축 절차·체크리스트·generated 규칙은 [implementation.md](implementation.md) §4로 흡수 (피벗 전 서비스 기준 테이블 레지스트리는 폐기). RLS 서사는 [adr/0011](adr/0011-rls-to-physical-isolation.md).
