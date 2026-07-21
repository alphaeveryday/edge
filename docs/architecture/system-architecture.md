# EDGE — System Architecture

> 원본: `EDGE_아키텍처_v0_2.pptx` — Slide 3 (System Architecture)
>
> **[설계 뷰]** 논리 설계 개요다. 현행 배포·계약의 권위는 SSOT([../context.md](../context.md))에 있다. 이 문서의 Service·Worker는 **논리 단위**이며, 실제 배포 모듈로의 대응은 아래 [논리 → 배포 모듈 매핑](#논리--배포-모듈-매핑)을 따른다. 충돌 시 SSOT 우선.

## 개요

Application Architecture의 논리 구성요소를 실제 시스템 컴포넌트(Client / Gateway / Service / Cache / Database / Scheduler / Worker)로 매핑한 다이어그램. **금융사 통제 환경**과 **EDGE 환경**이 물리적으로 분리되어 있으며, 두 환경은 금융사 측 **Relay Worker (Customer DMZ)** → EDGE **API Gateway** 경로로만 통신한다.

> **참고**: 다이어그램의 **API Gateway는 실제로 존재하는 별도 모듈이 아니라 개념적 표현**이다. 요청이 각 서비스로 라우팅되는 진입 지점을 나타내기 위한 논리적 구성요소로, 독립 배포되는 게이트웨이 컴포넌트가 있는 것은 아니다.

---

## 1. 금융사 통제 환경

### 구성요소

| 그룹 | 컴포넌트 |
|---|---|
| Client | Embed Widget (Customer Application), Customer Console |
| (금융사 소유) | Customer System — 위젯 요청을 받아 **온프렘 Publication Service**로 전달하는 금융사 구축 시스템 (요청은 금융사 환경 내에 머물며 vendor cloud를 호출하지 않는다, [../contracts/publication-api.md](../contracts/publication-api.md)) |
| Gateway | API Gateway (개념적 구성요소 — 실제 모듈 아님) |
| Service | Publication Service, User Service, Stats Service, Explanation Service, Review Service, Policy Service, Settings Service |
| Cache | Publication Cache |
| Database | User Database, Audit Log Database, Explanation Database, Review Database, Policy Database |
| Scheduler | Content Sync Scheduler |
| Worker | Intake Worker, Screening Worker, Relay Worker (Customer DMZ) |

### 주요 흐름

**제공(위젯) 경로**
```
Embed Widget → Customer System → Publication Service → Publication Cache → Explanation Database
```

**콘솔 경로**
```
Customer Console → API Gateway → 각 Service
├── User Service        → User Database
├── Stats Service       → Audit Log Database, Explanation Database
├── Explanation Service → Explanation Database
├── Review Service      → Review Database, Explanation Database
├── Policy Service      → Policy Database
└── Settings Service    → Policy Database, User Database
```

**반입(동기화) 경로**
```
Content Sync Scheduler → Intake Worker → Screening Worker → Explanation Database
                              └──────→ Relay Worker (Customer DMZ) → [EDGE] API Gateway
```

- Intake Worker가 Relay Worker(DMZ)를 통해 EDGE에서 분석 데이터를 가져오고, Screening Worker가 점검을 거쳐 Explanation Database에 적재

---

## 2. EDGE 환경

### 구성요소

| 그룹 | 컴포넌트 |
|---|---|
| Client | DNS, Organization Console |
| Gateway | API Gateway (개념적 구성요소 — 실제 모듈 아님) |
| Service | User Service, Tenant Service, Analysis Service, Tenant Sync Service |
| Cache | Analysis Result Cache |
| Database | User Database, Tenant Database, Analysis Database |
| Scheduler | Analysis Scheduler |
| Worker | Data Ingestion Worker, Data Processing Worker, Price Analysis Worker |

### 주요 흐름

**분석 파이프라인**
```
Analysis Scheduler → Data Ingestion Worker → Data Processing Worker → Price Analysis Worker → Analysis Database
```

**콘솔 경로**
```
Organization Console (→ DNS) → API Gateway → 각 Service
├── User Service     → User Database
├── Tenant Service   → Tenant Database
└── Analysis Service → Analysis Database
```

**금융사 반입 수신 (테넌트 동기화)**
```
[금융사] Relay Worker (Customer DMZ) → API Gateway → Tenant Sync Service → Analysis Result Cache → Analysis Database
```

- 콘솔용 조회(Analysis Service)와 테넌트 동기화 서빙(Tenant Sync Service)이 분리되어, Analysis Result Cache는 동기화 읽기 경로 전용

---

## Application ↔ System 컴포넌트 매핑

| Application Architecture | System Architecture |
|---|---|
| 가격 변동 설명 제공 API | Publication Service (+ Publication Cache) |
| 인증 API / 사용자 Repository | User Service / User Database |
| 대시보드 API / 감사 로그 Repository | Stats Service / Audit Log Database |
| 가격 변동 설명 API / 설명 Repository | Explanation Service / Explanation Database |
| 검수 API / 검수 Repository | Review Service / Review Database |
| 점검 기준 API / 점검 기준 Repository | Policy Service / Policy Database |
| 환경 설정 API | Settings Service |
| 점검 엔진 | Screening Worker |
| 가격 변동 설명 수집 (반입) | Content Sync Scheduler + Intake Worker |
| 가격 변동 설명 수집 (중계, DMZ) | Relay Worker (Customer DMZ) |
| 데이터 수집·처리 / 분석 엔진 / 분석 AI | Data Ingestion → Data Processing → Price Analysis Worker |
| 테넌트 API / 테넌트 Repository | Tenant Service / Tenant Database |
| 가격 변동 분석 API / 분석 Repository | Analysis Service / Analysis Database |
| 가격 변동 설명 수집 중계의 EDGE측 응답 (동기화 서빙) | Tenant Sync Service / Analysis Result Cache |

## 논리 → 배포 모듈 매핑

이 문서의 논리 Service·Worker는 설계 단위이고, 실제 배포는 더 적은 모듈로 묶인다. 아래가 논리명↔배포 모듈의 SSOT 브리지다 (배포 모듈의 현행 상세는 [../context.md](../context.md) §4).

**온프렘 (증권사 관리 환경)**

| 논리 (이 문서) | 배포 모듈 | 비고 |
|---|---|---|
| Publication Service | `apps/onprem/publication-api` | 구 serving-api ([adr/0031](../adr/0031-serving-to-publication.md)) |
| User·Stats·Explanation·Review·Policy·Settings Service | Tenant Console (`apps/onprem/tenant-console-api`·`-ui`) | Policy Service = 점검 **기준** 설정 |
| Screening Worker | Screening Engine | 점검 **실행** 엔진 (≠ Policy). 리네임·구현 예정 — 아래 [전진 예정 축](#전진-예정-축) 참조 |
| Relay Worker | Relay | **DMZ** 배포 — EDGE Cloud를 outbound pull |
| Intake Worker | Intake | **내부망** 배포 — Relay에서 재-pull |

> 위 온프렘 배포 모듈 중 **Screening Engine·Relay·Intake·Sync Agent는 아직 미구현**(walking skeleton 예정, [루트 README](../../README.md) 프로젝트 상태). 현행 코드로 존재하는 온프렘 모듈은 `publication-api`·`tenant-console-api`·`-ui`뿐이다.

**클라우드 (Vendor Cloud)**

| 논리 (이 문서) | 배포 모듈 | 비고 |
|---|---|---|
| User·Tenant·**Analysis** Service | Super Admin Console (`apps/cloud/super-admin-api`·`super-admin-ui`) | Analysis Service = 콘솔 **조회** 서비스 (≠ 파이프라인 `analysis-engine`) |
| Tenant Sync Service | `apps/cloud/tenant-sync-api` | |
| Data Ingestion·Processing·Price Analysis Worker | `apps/cloud/data-pipeline` + `analysis-engine` | 독립 Worker 아님 — 단일 Step Functions 4페이즈 raw→정제→feature→analyze ([adr/0028](../adr/0028-unified-pipeline-sfn.md)) |

> **API Gateway**는 이 문서(및 다이어그램)에서 논리적 진입 표현일 뿐 실제 모듈이 아니다 — 콘솔 내부에서 각 Service가 path로 호출되며, 공개 엣지 라우팅은 별도 ALB(호스트 1:1, [adr/0034](../adr/0034-host-per-edge-alb.md))가 담당한다. 구 `gateway` 모듈은 은퇴됨 ([adr/0032](../adr/0032-retire-gateway.md)).

## 전진 예정 축

아래 축은 이 뷰가 현행 SSOT보다 앞선 **채택된 목표 설계**다. 결정은 확정됐으나 `context.md`·`console-ia/` 전진은 후속 PR에서 반영되며, **그 전까지는 현행 SSOT가 기준**이다 (뷰가 조용히 이기지 않는다).

| 축 | 이 뷰 (목표) | 현행 SSOT | 전진 |
|---|---|---|---|
| 위젯 | 위젯 UI 임베드 접점 (빌드 산출물 납품, 서버 없음) | embed 위젯 제거·MTS 자체 구성 ([context.md](../context.md) §2, [scope.md](../scope.md)) | 후속 PR(위젯) |
| Sync 토폴로지 | Relay(DMZ)+Intake(내부망) 2모듈 상시 분리 | 단일 Sync Agent 기본, 2단은 망연계 옵션 ([context.md](../context.md) §4.2) | 후속 PR(Sync) |
| 콘솔 IA | [information-architecture.md](information-architecture.md) 재설계 | `console-ia/` 현행 메뉴 | 후속 PR(콘솔 IA) |
| 점검 엔진명 | Screening Engine | Compliance Engine (미구현) | 후속 PR(리네임) |

## 특징 요약

1. **환경 간 통신 단일화** — Relay Worker(Customer DMZ)만이 EDGE API Gateway와 통신하며, 금융사 내부 서비스는 EDGE에 직접 접근하지 않음
2. **읽기 경로 캐시 분리** — 고객 트래픽이 몰리는 제공 경로(Publication Cache)와 테넌트 동기화 경로(Analysis Result Cache)에 각각 전용 캐시 배치; 콘솔 조회(Analysis Service)는 DB 직접 접근
3. **점검의 비동기화** — Screening Worker가 반입 파이프라인 안에서 점검을 수행해 콘솔·제공 경로와 분리
4. **환경별 독립 인증/사용자 저장소** — 양쪽 환경 모두 자체 User Service/Database 보유
5. **동기화 서빙 전용 서비스** — 테넌트(Relay Worker) 대상 응답은 Tenant Sync Service가 전담해 콘솔용 Analysis Service와 워크로드 분리
