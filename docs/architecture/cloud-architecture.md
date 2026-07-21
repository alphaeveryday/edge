# EDGE — Cloud Architecture

> 원본: `EDGE_아키텍처_v0_2.pptx` — Slide 4 (Cloud Architecture)
>
> **[설계 뷰]** AWS 클라우드 배치의 논리 개요다. 현행 인프라의 권위는 [../../infra/terraform/README.md](../../infra/terraform/README.md), ALB·파이프라인 결정은 [../adr/0034](../adr/0034-host-per-edge-alb.md)·[../adr/0028](../adr/0028-unified-pipeline-sfn.md)에 있다. 충돌 시 SSOT 우선.
>
> ⚠️ **목표(prod) 토폴로지 — 현행 dev와 다름**: 이 구성도의 **다중 AZ 이중화**(NAT gateway ×2, RDS Primary/Standby, Replica Cache)와 **클라우드 캐시**(ElastiCache, Analysis Result Cache)는 목표 설계다. 현행 dev는 **단일 NAT·단일 AZ RDS(`multi_az=false`)**, 클라우드 캐시 **미배선**, prod 환경 **미구축**이다 ([../../infra/terraform/README.md](../../infra/terraform/README.md)). 현행 캐시는 온프렘 Publication Cache(Redis)뿐 ([../context.md](../context.md) §4.2).

## 개요

EDGE 클라우드(컨트롤 플레인)의 AWS 인프라 구성도. 고객사(Customer Environment)는 온프레미스로 유지되고, DMZ의 Relay Worker가 `edgesignal.dev` 도메인을 통해 AWS 측과 통신한다. AWS 측은 **파이프라인용 ECS 클러스터(Step Functions 기반)**와 **서빙용 ECS 클러스터**로 나뉘며, 2개 AZ에 걸친 3계층 서브넷(Public / App Private / Data Private) 구조를 갖는다.

---

## 1. Customer Environment (고객사 온프레미스)

| 구역 | 컴포넌트 |
|---|---|
| Channel Server | Widget UI — 투자자(Investor)가 접근 |
| Internal Control Server | Publication Service, Customer Console UI, Customer Console Service, Intake Worker, Screening Worker — 준법 감시인(Compliance Officer)이 콘솔 접근 |
| DB Server | 단일 DB (테넌트 내부 데이터베이스) |
| DMZ | Relay Worker |

**흐름**
- Investor → Widget UI → Internal Control Server(Publication Service)
- Compliance Officer → Customer Console UI/Service
- Internal Control Server → DB Server
- Internal Control Server → DMZ(Relay Worker)
- Relay Worker → `sync-dev.edgesignal.dev`:443 → AWS Route 53 → Tenant Sync ALB (아웃바운드 전용)

---

## 2. AWS Cloud (EDGE 컨트롤 플레인)

### 2.1 엣지 / 진입 계층

| 컴포넌트 | AWS 서비스 | 역할 |
|---|---|---|
| DNS | AWS Route 53 | `edgesignal.dev` 존. 진입은 **서비스별 호스트** — sync=`sync-dev.edgesignal.dev`(prod `sync.edgesignal.dev`), super-admin UI=`admin-dev.edgesignal.dev`(CloudFront)·API=`admin-api-dev.edgesignal.dev`(ALB) ([../adr/0034](../adr/0034-host-per-edge-alb.md) 호스트 1:1) |
| CDN | AWS CloudFront | 정적 자산 배포 (S3 오리진) |
| Internet Gateway | — | VPC 인터넷 진입 |
| ALB ×2 | Application Load Balancer | 용도별 분리 — **Tenant Sync Service용 ALB**와 **Super Admin Console Service용 ALB**가 각각 존재. 다이어그램에서는 가독성을 위해 하나로 표현했으나 실제로는 두 쌍 |

### 2.2 파이프라인 (AWS Step Functions workflow)

- **Scheduler**: AWS EventBridge → Step Functions 실행 트리거
- **ECS Cluster (파이프라인)** — VPC 내에서 Step Functions가 태스크를 단계별 실행

현행 SFN은 **4페이즈** ([../adr/0028](../adr/0028-unified-pipeline-sfn.md)):

| 페이즈 | Task (대표) |
|---|---|
| **raw 수집** | Ingest News/Price/Disclosure 등 — 전체 소스(FMP 재무·KIS NAV·ETF 구성종목 등)는 SFN 정의가 SSOT |
| **정제 (normalize)** | Process News/Price/Disclosure Task |
| **feature** | 지표·assertion·event·price-trigger 산출 — analyze의 입력 |
| **analyze** | Decomposing Prices, Generating Explanations |

- 파이프라인 태스크는 **AWS S3**(데이터 적재)와 **ECR**(컨테이너 이미지)을 사용하고, 결과를 Data subnet group의 RDS에 적재

> ⚠️ analyze는 **feature 산출물**(assertion·event·price-trigger)에 의존한다 — 런북·리뷰 시 이 의존을 놓치지 말 것. 태스크 목록은 대표값이며 전체는 `infra/terraform/modules/data-pipeline` SFN 정의가 SSOT.

### 2.3 서빙 (ECS Cluster)

2개 AZ에 동일 구성으로 배치:

| 서브넷 | 컴포넌트 |
|---|---|
| Public subnet (AZ-a / AZ-b) | NAT gateway ×2 — App Private subnet의 아웃바운드 경로 |
| App Private subnet (AZ-a / AZ-b) | Tenant Sync Service, Super Admin Console Service (ECS 서비스) |
| Data Private subnet (AZ-a) | Primary Cache (AWS ElastiCache), Primary RDB Database (AWS RDS) |
| Data Private subnet (AZ-b) | Replica Cache (AWS ElastiCache), Standby RDB Database (AWS RDS) |

- Data Private subnet 2개는 **Data subnet group**으로 묶이며, Primary ↔ Standby 간 복제
- 서비스 흐름: Tenant Sync ALB → Tenant Sync Service → **읽기 캐시(Analysis Result Cache)** → RDS; Super Admin ALB → Super Admin Console Service → **RDS 직접**. 캐시는 **테넌트 동기화 읽기 경로 전용**이고 콘솔 조회는 캐시를 경유하지 않는다 ([system-architecture.md](system-architecture.md) 특징 요약)

> ⚠️ **이 서빙 구성은 목표(prod) 토폴로지** — 현행 dev는 단일 NAT·단일 AZ RDS이고 클라우드 캐시(ElastiCache)는 미배선. 위 배너 참조.

---

## 트래픽 경로 요약

1. **테넌트 동기화(반입)**: Relay Worker(고객사 DMZ) → Route 53(`sync-dev.edgesignal.dev`) → Internet Gateway → Tenant Sync용 ALB → Tenant Sync Service → Cache/RDS
2. **슈퍼 어드민 콘솔**: Super Admin → CloudFront(`admin-dev.edgesignal.dev`, 정적 UI 자산) + Super Admin Console용 ALB(`admin-api-dev.edgesignal.dev`, API) → Super Admin Console Service
3. **분석 파이프라인**: EventBridge → Step Functions → ECS Task (raw → 정제 → **feature** → analyze) → S3 / RDS

> ⚠️ 방화벽 화이트리스트·인증서는 **각 서비스 FQDN 기준**이어야 해당 ALB에 도달한다 — apex `edgesignal.dev`로는 sync ALB에 도달하지 못한다 ([../adr/0034](../adr/0034-host-per-edge-alb.md) 호스트 1:1).

## 특징 요약

1. **클러스터 분리** — 상시 서빙(ECS 서비스)과 배치 파이프라인(Step Functions RunTask)을 별도 ECS 클러스터로 운영
2. **멀티 AZ 이중화** — 서브넷 3계층 × 2 AZ, RDS Primary/Standby, ElastiCache Primary/Replica
3. **아웃바운드 전용 테넌트 연동** — 고객사에서 클라우드로의 pull만 존재, 클라우드에서 고객사로의 inbound 없음
4. **프라이빗 격리** — 앱·데이터 계층은 프라이빗 서브넷에 두고 NAT gateway로만 아웃바운드
5. **ALB 용도별 분리** — 테넌트 동기화 트래픽(Tenant Sync)과 운영 콘솔 트래픽(Super Admin Console)이 별도 ALB를 사용해 진입 경로부터 분리
