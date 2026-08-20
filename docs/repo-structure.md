# edge — 저장소 구조

> 루트 README 쇼케이스 재구성으로 옮겨온 문서다. 저장소 구조·워크스페이스·모듈 역할·데이터 흐름의 SSOT.

세 가지 런타임(JVM · Node · Python)을 한 저장소에서 관리하는 폴리글랏 모노레포입니다.
실제 코드는 `src/` 아래에 있으며, 배포되는 실행 단위는 `apps/`, 가져다 쓰는 공유 코드는 `libs/`에 둡니다.

> **프로젝트 상태 — 하이브리드 피벗 재편 중.** JVM 앱(tenant-console-api·super-admin-api·tenant-sync-api·publication-api·sync-agent·intake)은 Spring Boot로 스캐폴드되어 빌드·기동되며, `libs/schema`(Flyway)·`libs/jvm-common`(공통 응답 규약)도 채워졌습니다. 벤더 서빙 embed widget 서버(widget-api)는 하이브리드 온프렘 피벗([ADR-0010](adr/0010-hybrid-onprem-pivot.md))으로, 클라우드 gateway는 [ADR-0032](adr/0032-retire-gateway.md)로 삭제됐고 (위젯 **UI 자체는 빌드 산출물로 납품** — [ADR-0035](adr/0035-widget-ui-build-artifact.md), 벤더 실행 서버 없음), 배포는 **아티팩트 2종(edge-cloud / edge-onprem)** 경계로 재편됩니다([docs/implementation.md](implementation.md) §1). 동기화·반입 경로(sync-agent=DMZ Pull·검증, intake=내부망 수신·저장 — [ADR-0036](adr/0036-sync-agent-intake-topology.md), screening-worker=정책 평가·상태 분기·자동 게시)까지 구현됐습니다.

## 한눈에 보기

```
src/
├── apps/                     # 배포되는 실행 단위 (플레인별 그룹 — ADR-0029)
│   ├── cloud/                #   edge-cloud (벤더 운영)
│   │   ├── tenant-sync-api/  # JVM    · Sync Agent Pull 표면 (cursor delta)
│   │   ├── super-admin-api/  # JVM    · 운영자용 · cross-tenant · 최고 권한
│   │   ├── super-admin-ui/   # Node   · 플랫폼 운영자 콘솔 (cross-tenant)
│   │   ├── data-pipeline/    # Python · 파이프라인 SFN raw→정제→feature 페이즈
│   │   └── analysis-engine/  # Python · 분봉 트리거 큐 상주 소비자 → 분석 결과 DB 저장
│   └── onprem/               #   edge-onprem (증권사 관리 환경)
│       ├── tenant-console-ui/  # Node · 테넌트 검수·정책 콘솔
│       ├── tenant-console-api/ # JVM  · 테넌트용 · 읽기/쓰기
│       ├── sync-agent/         # JVM  · DMZ — Cloud pull + 체크섬 검증 (ADR-0036)
│       ├── intake/             # JVM  · 내부망 — Raw Event Store 멱등 적재
│       ├── screening-worker/   # JVM  · 점검 실행 — 상태 분기·자동 게시 (state-machine)
│       └── publication-api/        # JVM  · MTS 조회 표면 (Published만)
├── libs/                     # 가져다 쓰는 공유 코드 (플레인 무관 공유)
│   ├── schema/               # ★ DB 스키마 = 단일 진실 공급원(SSOT)
│   │   ├── migrations-cloud/ #   Flyway cloud 세트 (+ migrations-onprem/ = 온프렘 세트)
│   │   └── generated/        #   스키마 파생물 — 생성기가 있는 것은 물리 ERD(DBML) 둘뿐
│   ├── jvm-common/           # JVM    · 공통 응답 규약(apipayload)·예외 매핑 + 공유 도메인
│   ├── ui-kit/               # Node   · 두 UI 공유 디자인 시스템
│   ├── py-common/            # Python · 공통 유틸
│   └── ontology/             # Python · 온톨로지 SSOT (존재 4층 어휘 리소스+로더)
├── settings.gradle           # JVM 루트 (Groovy DSL 멀티모듈)
├── pnpm-workspace.yaml       # Node 루트
└── pyproject.toml            # Python 루트
```

위 트리는 `src/`(코드) 내부다. 저장소 최상위에는 그 밖에 `docs/`(설계 문서) · `tests/`(검증 인프라) · `demo/`(데모 — 가상 MTS 정적 화면·mock-broker 데모 서버·온프렘 박스 compose `onprem/`) · `scripts/`(개발 스크립트 — 로컬 전체 스택 기동 등) · `.dev/`(로컬 스크래치, git 미추적) · `out/`(빌드 산출물, git 미추적) · `.claude/`(에이전트 설정)가 있다.

## 런타임별 워크스페이스

각 런타임은 독립된 루트 설정 파일로 자기 모듈만 묶습니다.

JVM은 `src/settings.gradle`(Groovy DSL) 단일 멀티모듈 빌드다. 현재 `libs:schema`·`libs:jvm-common`과 7개 앱(tenant-console-api·tenant-sync-api·publication-api·super-admin-api·sync-agent·intake·screening-worker)이 등록되어 있다. 배포는 여전히 서비스별 독립(각 앱이 자기 bootJar·이미지).

| 런타임 | 루트 설정 | 포함 모듈 |
|---|---|---|
| JVM | `src/settings.gradle` | schema · jvm-common · tenant-console-api · tenant-sync-api · publication-api · super-admin-api · sync-agent · intake · screening-worker |
| Node | `src/pnpm-workspace.yaml` | tenant-console-ui · super-admin-ui · ui-kit |
| Python | `src/pyproject.toml` | analysis-engine · data-pipeline · py-common · ontology |

## apps — 배포 단위

| 앱 | 런타임 | 아티팩트 | 역할 |
|---|---|---|---|
| `tenant-console-ui` | Node | **edge-onprem** | 테넌트 검수·정책 콘솔 (증권사 관리 환경 배포, 디자인 v0.2 기준 재구축 — [console-ia](console-ia/tenant-console.md)와의 IA 정렬은 후속). 전 도메인이 tenant-console-api 호출 — UI 자체 mock 레이어 없음 |
| `super-admin-ui` | Node | **edge-cloud** | 플랫폼 운영자용 콘솔 (**cross-tenant**). 전 도메인이 super-admin-api 호출 — 데이터 경로에 mock 없음. 단 `src/mock/preview.ts` 는 **실 데이터가 0건일 때 화면을 검수하기 위한 미리보기 픽스처**로 별도다(repository 를 대체하지 않는다, ALPHA-738) |
| `tenant-console-api` | JVM | **edge-onprem** | 테넌트용 API — 검수 표면(Review Queue 목록·승인·반려, 승인=전이+재발행 단일 트랜잭션) + 인증·인가(데모 자체 계정·세션·매 요청 원장 재검증 fail-closed, [permission-matrix](console-ia/permission-matrix.md)) + 사용자 관리(등록·목록·비활성화, 실 DB + 감사 로그 `console_action_log` — ALPHA-119) + 가격 변동 설명 조회(explanations 목록·상세·수신 상태 원장 실조회, 쓰기는 mock 잔존 — ALPHA-607) + 나머지 콘솔 화면 표면(현재 `mock` 패키지 반환, 도메인별 DB 전환 예정 — ALPHA-513). 정책은 후속 |
| `tenant-sync-api` | JVM | **edge-cloud** | Sync Agent가 Pull하는 Event Bundle 제공 — cursor 기반 delta ([contracts/sync-protocol.md](contracts/sync-protocol.md)). tenant_delivery(outbox) 조회로 번들 조립, mTLS 인가는 후속 |
| `sync-agent` | JVM | **edge-onprem** | DMZ — tenant-sync-api outbound Pull + 번들 체크섬 검증, 내부망 무변형 전달. DB 접근 없음 ([ADR-0036](adr/0036-sync-agent-intake-topology.md)) |
| `intake` | JVM | **edge-onprem** | 내부망 — 검증된 번들을 Raw Event Store(`received_bundle`)에 멱등 적재, committed cursor 권위 |
| `screening-worker` | JVM | **edge-onprem** | 점검 실행 — 미점검 번들 파싱·정책 평가(NEW=활성 정책 룰·임계값으로 AUTO_PUBLISHED/REVIEW_REQUIRED/BLOCKED 분기, 근거는 screening_check — ALPHA-429, 무효화=즉시 비노출, 정정(CORRECTION)은 폐지 유형으로 fail-loud — ADR-0044) |
| `publication-api` | JVM | **edge-onprem** | MTS 위젯이 직접 호출하는 조회 표면 — **Published만 반환**, 고객 식별 비수취 ([contracts/publication-api.md](contracts/publication-api.md)·[ADR-0053](adr/0053-widget-direct-serving-no-personalization.md)). 온프렘 Published Store(PG) 조회 |
| `super-admin-api` | JVM | **edge-cloud** | 운영자용 API. **cross-tenant 읽기/쓰기**, 최고 권한 표면 — 운영자 인증(config 부트스트랩·세션·fail-closed 인가) + 콘솔 화면 표면 4종(tenants 는 JPA 로 실 `tenant` 테이블 — ALPHA-526, **sources 는 운영 원장 `ops_*` 읽기 전용 조회** — ALPHA-514, **analyses 읽기는 설명 원장 `explanation_*` 읽기 전용 조회** — ALPHA-601, **analyses 쓰기는 무효화 단독**(게시본 WITHDRAWN 전이 + `tenant_delivery` INVALIDATION 발번 + `admin_activity_log` 감사) — ALPHA-440·737, session 은 인증 세션 주체 투영 — ALPHA-608) + **콘솔 규칙 엔진의 사실 표면**(`GET /api/v1/console/facts` 하루 사실 + `GET /api/v1/console/trends/entity-resolution`·`intraday-analysis` 최근 일별 사실 — 판정은 클라이언트. 축과 추이 계약은 [계약 문서](contracts/console-facts-api.md)가 정본 — [ADR-0050](adr/0050-console-facts-endpoint.md) — ALPHA-738·1001·1005) |
| `data-pipeline` | Python | **edge-cloud** | 통합(시장) 파이프라인 SFN 의 raw 수집→정제→feature 페이즈 담당 + 레인 SFN 3종 단독 소유(뉴스·공시·장중 수급) |
| `analysis-engine` | Python | **edge-cloud** | 분봉 트리거 큐를 소비하는 상주 서비스 → 분석 결과를 DB에 저장 (SFN 페이즈 아님 — ALPHA-806) |

sync-agent(DMZ Pull·검증) · intake(내부망 수신·저장) · screening-worker(상태 분기·자동 게시)는 **edge-onprem**으로 구현됐습니다([ADR-0036](adr/0036-sync-agent-intake-topology.md) · [docs/implementation.md](implementation.md) §1). `tenant-sync-api`는 별도 엣지로 mTLS 직접 종단해 노출됩니다([ADR-0032](adr/0032-retire-gateway.md)로 클라우드 gateway 은퇴).

### 표면 분리
- **콘솔 경로**: `tenant-console-ui` → `tenant-console-api` (읽기/쓰기, 한 테넌트 범위 — 온프렘에서 UI·API 동거)
- **운영 경로**: `super-admin-ui` → `super-admin-api` (cross-tenant 읽기/쓰기, 최고 권한)

클라우드 gateway는 은퇴했습니다([ADR-0032](adr/0032-retire-gateway.md)) — super-admin 공개 도달이 필요해지면 ALB 직결(listener rule)로 재도입하고, admin은 운영자(소수·알려진 집합) 전용이라 망 수준(VPN/IP allowlist)으로 제한합니다. 고객 접점은 벤더가 아니라 증권사 MTS/HTS → 온프렘 Publication API 경로입니다. 신뢰 경계 상세는 [docs/context.md](context.md)·[ADR-0008](adr/0008-super-admin-console.md) 참고.

## libs — 공유 코드

| 라이브러리 | 런타임 | 역할 |
|---|---|---|
| `schema` | — | **DB 스키마 단일 진실 공급원(SSOT)**. 마이그레이션과 그 파생물(물리 ERD)을 관리 — 언어별 모델 생성기는 아직 없다 |
| `jvm-common` | JVM | 공통 API 응답 규약(apipayload — `ApiResponse`·`BaseErrorCode`·`GeneralException`)·예외→공통 응답 포맷 매핑(`ExceptionAdvice`, auto-configuration 으로 웹 앱 활성) + 공유 도메인 모델·Cloud Event Store(`explanation_result` 등) 접근 로직 |
| `ui-kit` | Node | 콘솔 UI 공유 디자인 시스템 — EDGE 디자인 토큰·컴포넌트 CSS·React 프리미티브 (소스 export 패키지) |
| `py-common` | Python | Python 공통 유틸 |
| `ontology` | Python | **온톨로지 SSOT**(`edge_ontology`) — 존재를 네 층으로 나눈 선험적 어휘. `entity`(실체 종별·기관 레지스트리) · `attribute`(속성 모형·공용 재무풀) · `relation`(역할 어휘·종별 결속) · `process`(53 사건 타입·술어·라이프사이클·thread 계약). 실제 사건 인스턴스와 절차적 지식은 이 lib 밖(data-pipeline·analysis-engine) 소관. 갱신은 실험실(event-ontology repo) 확정본을 통째 교체 + 어휘 변경 시 `ONTOLOGY_VERSION` 개정(ALPHA-539) |

### schema — 단일 진실 공급원(SSOT)
DB 스키마를 `schema/` 한 곳에서 정의합니다.
- `migrations-cloud/`(cloud)·`migrations-onprem/`(온프렘) — Flyway 세트 2개, 아티팩트 분리(ADR-0016). 스키마 변경은 여기서만 관리합니다. 실행은 [`libs/schema`](../src/libs/schema/README.md)의 Gradle Flyway 태스크로.
- `generated/` — 스키마로부터 만드는 파생물을 커밋합니다. 현재 있는 생성기는 **물리 ERD** 하나입니다(`scripts/generate-erd.sh` → `physical-erd.dbml`·`physical-erd-onprem.dbml`, pre-commit 훅 + `schema-validate` CI 가 재생성해 커밋본과 대조 — ALPHA-783). **각 언어용 모델 생성기는 아직 없어**, JVM·Python 소비자는 Flyway SQL 을 계약 SSOT로 직접 따릅니다.

## 데이터 흐름

```
[스케줄러] ─→ Planner ─→ 파이프라인 SFN: raw 수집 ─→ 정제 ─→ feature ──────────→ DB
           (원장 기록 후 시작)         └──── data-pipeline ────┘                    │
                                                                                │
[분봉 트리거 큐] ─→ analysis-engine (상주 소비자) ─→ 설명 생성 ──────────────────→ DB
                                                                                │
   콘솔:  tenant-console-ui → tenant-console-api (읽기/쓰기, 한 테넌트) ─┘
   운영:  super-admin-ui → super-admin-api (읽기/쓰기, cross-tenant) ─┘

   schema(Flyway SQL = 현재 SSOT) ─→ DB 계약 ─→ 모든 JVM/Python 모듈이 공유   (generated 모델은 후속 도입)
```

- `data-pipeline`이 raw 수집→정제→feature 페이즈에서 외부 데이터를 raw lake에 보존·정규화하고, feature 산출물(가격 트리거·종목 마스터 등)을 DB에 적재합니다.
- `analysis-engine`이 **분봉 트리거 큐를 소비하는 상주 서비스**로 돌며(SFN 페이즈가 아닙니다 — ALPHA-806) feature 산출물만 읽어 분석하고, Cloud Event Store(`explanation_result` 등)로 DB에 저장하며, 게시(PUBLISHED)와 같은 트랜잭션으로 sync outbox(`tenant_delivery`)에 테넌트별 NEW 를 발번합니다 ([ADR-0028](adr/0028-unified-pipeline-sfn.md), ALPHA-493).
- **운영 원장**(ALPHA-530): 스케줄러는 SFN 을 직접 시작하지 않고 **Planner**(`data-pipeline` 의 `plan-run`)를 띄웁니다 — 실행 **전에** 예정 작업(`ops_*` 테이블)을 Postgres 에 남기고 SFN 을 시작해, SFN 이 안 떠도 미실행을 탐지합니다. **Reconciler**(`reconcile`)가 예정↔실제(SFN/ECS 증거)를 대조합니다. 실행을 제어하지 않는 관측 projection 입니다([data-pipeline/README](../src/apps/cloud/data-pipeline/README.md#운영-원장--expected_taskplannerreconciler-alpha-530)).
- API 계층(`tenant-console-api`/`super-admin-api`)이 DB를 읽어 UI에 제공하며, Cloud Event Store 접근은 `jvm-common`이 담당합니다.
- 고객 대면 흐름(Cloud Event Store → Tenant Sync API → 온프렘 Sync Agent(DMZ) → Intake(내부망) → Screening → Publication API)이 관통합니다([docs/context.md](context.md) §3) — Screening 은 활성 정책(policy_version·screening_rule)을 평가해 AUTO_PUBLISHED/REVIEW_REQUIRED/BLOCKED 로 분기하며(ALPHA-429), 정정(CORRECTION) 전달은 폐지됐고 무효화(INVALIDATION)가 유일한 사후 조치이며([ADR-0044](adr/0044-correction-abolition.md)), 점검 Audit 은 후속(ALPHA-431)입니다.
