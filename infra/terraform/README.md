# infra/terraform

`edge` 의 AWS 인프라를 코드로 정의한다. **완전 그린필드** — 도메인 등록(Route53 존·NS 위임)만 수동이고, 그 아래(SSL·네트워크·컴퓨트·데이터·CDN)는 전부 Terraform 이 소유한다. 다른 계정/region 에서도 `tfvars` 만 바꿔 재현된다.

> 배포 토폴로지 결정은 [docs/adr/0009](../../docs/adr/0009-aws-deployment-topology.md), 신뢰 경계는 [docs/context.md](../../docs/context.md) §3.

## 구조 — 단계(phase) 스택 + 모듈

수명·blast-radius 로 3단계 스택을 나눈다. 각 스택은 **독립 state·독립 apply**.

```
infra/terraform/
├── bootstrap/          # 원격 state 그릇(S3 버킷 + 네이티브 락). 자체 state=로컬, 계정당 1회
├── foundation/         # 계정 전역·장수명: Route53 존 · 와일드카드 ACM ×2(apne2·us-east-1) · ECR · GitHub OIDC provider
├── envs/
│   ├── dev/            # 실 벤더 클라우드 (모듈을 엮음, 구체값은 terraform.tfvars)
│   └── demo-onprem/    # 가상 온프렘 데모 스택 (EC2 + 프록시 사이트 2종(MTS·콘솔), dev와 별도 state — ADR-0033)
└── modules/
    ├── network/            # VPC, 3-tier 서브넷(public·private/compute·data/격리), IGW, NAT, S3 gateway endpoint(private만), AZ override
    ├── ecs-cluster/        # ECS 클러스터 + Service Connect + Fargate CP
    ├── ecs-service/        # 재사용 상시 서비스: task def + service + SG + IAM + 로그
    ├── alb/                # 공개 엣지 ALB (호스트 단위 1:1, mTLS verify 옵션 — ADR-0034. 호출자: sync·super-admin ALB)
    ├── rds/                # PostgreSQL(private·관리형 비밀번호·로테이션 창 고정) + 관측(FreeableMemory 경보·Performance Insights — 경보는 data-pipeline 토픽으로, ALPHA-919)
    ├── schema-migrate/     # Flyway one-off task (ECR은 foundation 입력으로 decoupled)
    ├── github-oidc-deploy/ # GitHub Actions OIDC 배포 역할(최소 권한)
    ├── pipeline/           # 구 news-pipeline SFN 의 존치 자원 — data-pipeline 이 쓰는 lake S3 버킷만 소유 (ALPHA-549)
    ├── data-pipeline/      # 레인별 Step Functions 배치 4종 — 시장 SFN(raw→normalize→feature) + 뉴스(ALPHA-553) + 공시(ALPHA-722·724) + 장중 수급(ALPHA-769) + 분봉 트리거 상주 소비자 3종(설명·분봉·수급) (data-pipeline·analysis-engine 이미지·S3 lake·시크릿·스케줄러)
    ├── static-site/        # S3(프라이빗)+CloudFront(OAC)+Route53 alias — 클라우드 프론트 CDN
    ├── proxy-site/         # CloudFront(커스텀 오리진 창문)+Route53 alias — 데모 표면(박스 서빙) — ALPHA-632
    └── demo-onprem/        # 가상 온프렘 데모 박스: EC2 + SG + IAM(SSM·ECR) + user-data(docker/compose 부트스트랩) — ADR-0033
```

`ecs-service` 를 super-admin-api·tenant-sync-api 가, `static-site` 를 super-admin UI 가 쓴다(S3 정적 호스팅이 실물인 클라우드 표면 전용). 데모 표면(MTS·검수 콘솔)은 `proxy-site`(CloudFront→박스 오리진 창문)를 동일 재사용한다 — 서빙 원칙(ALPHA-632): 모든 데모 표면은 박스가 서빙한다. (tenant-console 은 온프렘 플레인이라 cloud 정적사이트 없음 — ADR-0032.)
(tenant-console-api 는 onprem 플레인이라 dev ECS 에서 제거 — 실 배포처는 데모 박스 compose, ADR-0029·0033.)
두 API 는 각자 전용 ALB 뒤에 있다 — tenant-sync-api=`sync-dev.edgesignal.dev`(mTLS 예정), super-admin-api=`admin-api-dev.edgesignal.dev`. 진입점은 호스트 단위 1:1, ALB 경로 라우팅 없음(ADR-0034). 단 admin 콘솔 CDN(`admin-dev`)은 `/api/*` 를 admin ALB 오리진으로 프록시한다(same-origin 세션 쿠키, ALPHA-615) — ALB 계층의 1:1 은 그대로다.

## 설계 요지

- **단계 스택** — bootstrap(state) → foundation(zone·ECR·OIDC·ACM) → envs. env 는 foundation 자원을 이미지 URI·`data`(ACM/OIDC/ECR 조회)로 **느슨하게** 참조 → remote_state 강결합 없음. **apply 순서: foundation → env.**
- **와일드카드 ACM** — `*.edgesignal.dev` 을 리전당 1장(ALB=apne2, CloudFront=us-east-1). 새 서브도메인 추가 시 인증서 재발급 0.
- **네트워크 3-tier** — public(ALB·NAT) / private=compute(ECS, NAT 아웃바운드) / **data=RDS 격리(아웃바운드 없음)**. AZ `a·c`.
- **클러스터 분리** — 상시 API(`edge-dev-service`) / 배치(`edge-dev-worker`).
- **배치 = Step Functions** — 시장 레인 data-pipeline(raw→normalize→feature→analyze 4페이즈, 구 analysis-engine SFN 흡수 — ALPHA-408)을 `ecs:runTask.sync` 로 오케스트레이션(재시도·실패알림). 4페이즈는 **시장 SFN 만의 형태**다 — 뉴스·공시·장중 수급 레인은 analyze 페이즈가 없는 별도 state machine 이다(뉴스는 태깅·이벤트 조립까지, 공시·장중 수급은 적재까지. 위 모듈 트리). 구 임시 news-pipeline SFN 은 ALPHA-549 에서 제거 — `pipeline` 모듈은 lake 버킷만 존치.
- **비밀번호는 코드/state 에 없음** — RDS 관리형 시크릿, 외부 키는 Secrets Manager(값 수동 주입).
- **로테이션 시각은 우리가 정한다** — 관리형 시크릿의 기본값(`AutomaticallyAfterDays: 7`)은 **주기만** 주고 시각은 AWS 가 임의로 잡는다. 그 시각이 2026-08-14 장중 10:08 에 떨어져 전 워커가 인증 실패로 죽었고 분봉이 5시간 멎었다(ALPHA-986). `modules/rds` 의 `aws_secretsmanager_secret_rotation` 이 창을 **토요일 09:00\~12:00 KST** 로 못박는다. 로테이션은 돌고 있는 태스크를 반드시 죽인다 — 비밀번호가 ECS `secrets` 로 기동 시 1회 주입되기 때문이고, 그래서 처방이 재시도가 아니라 창 이동이다.

## 사용

```bash
# 최초 1회 — 원격 state 그릇
cd bootstrap && terraform apply

# 순서 엄수: foundation 먼저(ACM·ECR·OIDC), 그다음 env
cd ../foundation && terraform apply
cd ../envs/dev  && terraform apply
```

- **envs/dev 는 Terraform CD 로 배포된다(ALPHA-311)**: `envs/dev/**`·`modules/**` 를 바꾼 PR 이 `terraform-plan.yml`(read-only 역할 `edge-tf-plan`)로 plan 을 PR 코멘트에 게시하고, dev 머지 시 `terraform-apply.yml`(`edge-tf-apply`, trust=`ref:refs/heads/dev`)이 apply 한다. 두 역할은 foundation `tf-cd.tf` 소유. 위 수동 apply 는 **bootstrap·foundation**(CD 대상 아님) 및 env 브레이크글래스용이다.
  - 🔴 **`modules/rds` 는 머지 시각을 골라야 한다 — 장 마감 후에 머지하라.** 이 모듈이 `apply_immediately = true` 라(ALPHA-924) **머지 = 즉시 apply = 즉시 DB 재부팅**이고, 재부팅은 이 DB 를 쓰는 1분 레인 5종을 함께 세운다. 그리고 즉시 반영은 이번 변경만이 아니라 **AWS 대기 큐 전체**를 함께 터뜨리므로(`auto_minor_version_upgrade` 가 사람 손 없이 큐를 채울 수 있다), 머지 직전에 `aws rds describe-db-instances --db-instance-identifier edge-dev --query 'DBInstances[0].PendingModifiedValues'` 가 `{}` 인지 확인하라. 다른 모듈에는 해당 없다.
- **envs/demo-onprem** 은 apply CD 밖이다(ADR-0033) — PR 에서 오프라인 `terraform validate`(전용 `terraform-validate-demo.yml`, creds 불필요)로만 검증하고, apply 는 수동. dev plan(`terraform-plan.yml`, OIDC creds)과 분리해 데모만 바꾼 PR 이 dev 자격/drift 에 묶이지 않게 한다. 데모 런타임(compose·sync·CloudFront 오리진)은 개통 완료(ALPHA-445·627·632 — 현황은 아래 "미구축" 절).
- 상태는 **S3 원격**(`edge-tfstate-393229433969`, 네이티브 락). backend 는 `foundation/backend.tf`·`envs/dev/backend.tf`·`envs/demo-onprem/backend.tf`(같은 버킷, 다른 key — 데모/실클라우드 격리).
- env 를 foundation 전에 돌리면 `data` 소스에서 실패한다 — 그게 순서를 강제하는 안전장치.
- foundation 이 소유해야 하는 ECR 이 AWS 에 이미 수동 생성돼 있으면, 첫 apply 전에 해당
  repository 를 foundation state 로 import 한다(예: `edge/pipeline`). clean account 는
  foundation 이 직접 생성한다.
- 이미지 태그: `terraform.tfvars` 의 `*_image` 가 TF 소유 baseline. 앱 CD(`deploy-<app>.yml`)가 semver 태그를 올린다.
  서비스의 실행 task 정의는 CD 소유라 TF 가 되돌리지 않는다(`ecs-service` 의 `ignore_changes = [task_definition]`);
  `terraform.tfvars` 핀은 신규 생성 시 baseline 으로만 쓰인다.
  `data-pipeline` 배치 이미지는 `deploy-data-pipeline.yml` 이 기존 `edge/pipeline` 에 `{git-sha,data-pipeline-latest}` 를 push 하고,
  raw ingest task definition 은 `data-pipeline-latest` 를 참조한다.

## 현재 상태 (2026-07-04)

인프라는 **구조 완성 + apply 됨**. 다만 아래는 의도적으로 꺼두었거나 비어 있다.

### 🔴 의도적 off (준비되면 켠다)

| 기능 | 상태 | 켜는 법 |
|------|------|---------|
| **알림 이메일**(파이프라인 실패 + RDS 경보) | ✅ 확인 완료 — 구독 활성(실측 2026-07-20, 구독 ARN 발급됨) | `pipeline_alarm_email` 기본값(변경 시 여기) |
| **super-admin ALB 보호** | WAFv2 부착됨(ALPHA-297 — AWS Managed CommonRuleSet·KnownBadInputs, 차단 동작·CloudWatch 메트릭). IP 제한은 미적용(콘솔 API 표면 노출 — tenants 는 이제 실 `tenant` DB, ALPHA-526). 앱 인증(AdminAuthFilter fail-closed)은 있으나 dev 시크릿 미배선으로 닫힘 | 앱 인증 본격화(ALPHA-474)·`allowed_cidrs` 운영 판단·커스텀 룰/레이트리밋 후속 |
| **sync mTLS** | off — trust store 미주입(엔드포인트 공개 도달, dev 스텁·시드 데이터 전제) | CA·번들 준비(ALPHA-447) 후 `sync_mtls_trust_store_arn` 주입 |
| **오토스케일링** | `analysis-consumer` **만** 붙었다(ALPHA-912 — SQS 잔여 일감 계단, `modules/data-pipeline/analysis_autoscaling.tf`). 나머지 서비스는 없음 | 상한은 성능이 아니라 공유 RDS 가 정한다 — `analysis_consumer_max_capacity` 를 실측으로 올린다 |
| **NAT** | dev 단일 공유(`single_nat_gateway`) | prod 은 AZ당 1개 |

> ⚠️ `pipeline_alarm_email` 이 `null` 이면 SNS 구독 리소스가 `count=0` 으로 **아예 안 생겨** 실패
> 알림이 구독자 없는 토픽으로 사라진다 — "구독 없음"이 아니라 **알림 유실**이다. ALPHA-389 착수
> 전까지 실제로 그 상태였고(라이브 토픽 구독자 0), data-pipeline 정제가 run 스코프로 바뀐 뒤로는
> 실패 런의 raw 를 사람이 명시 재처리해야 하므로 이 알림이 그 절차의 트리거다.
> ⚠️ 유실되는 것은 파이프라인 실패 통보만이 아니다 — `modules/rds` 의 둘이 같은 토픽에 얹혀
> 있다: **메모리 고갈 경보**(ALPHA-919, 죽기 **전** 예고 — 그것도 느린 하강 형태에서만)와
> **RDS 이벤트 구독**(ALPHA-928, 재시작·복구·`critically low on memory` 를 RDS 가 직접 민다).
> 이 값을 비우면 **DB 가 죽었다는 통보까지** 함께 사라진다. 둘은 다른 축이다 — 알람은 죽음을
> 못 잡는다(2026-08-10 22:14 사망에 발화 0건).

### ⚪ 비어 있음 (off 아님 — 채워야 함, CD/수동 몫)

- 앱 ECR 이미지(push), 프론트 S3 콘텐츠 3개(build sync) — 백엔드(super-admin-api·tenant-sync-api)·data-pipeline·프론트 3종은 CD(`deploy-<app>.yml`·`deploy-data-pipeline.yml`·`deploy-<ui>.yml`)가 채운다. tenant-sync-api 최초 이미지는 `deploy-tenant-sync-api` 수동 실행(workflow_dispatch)으로 부트스트랩

### 🔮 미구축 (후속 증분)

- **데모 온프렘 런타임** — terraform(EC2·MTS 사이트)은 스캐폴드됨(ADR-0033), 온프렘 박스 compose 는 `demo/onprem/docker-compose.yml`(ALPHA-444 — 고객경로 7서비스 + 검수 콘솔 co-host 2(tenant-console-api·nginx `tenant-console-ui`, ALPHA-554), ECR 이미지 참조, sync-agent→실 cloud). 데모 서빙(ALPHA-632)은 `proxy-site` 모듈 2개 인스턴스 — MTS(`demo-mts.edgesignal.dev` → 박스 `:8080` mock-broker, 정적은 이미지 내장; 설명 조회 `/api/v1/*` 만 별도 behavior 로 박스 `:8084` publication-api 직행 + 쿠키·인증 헤더 strip — ADR-0053, ALPHA-992)·검수 콘솔(`demo-console.edgesignal.dev` → 박스 `:8090` nginx)이며, 콘솔 진입은 로그인 화면(ALPHA-626)이 게이트한다 — SSM 터널은 비상 경로(ALPHA-627, 구 127.0.0.1 전용 바인딩 폐기). 구 MTS S3 버킷·sync 갈래는 제거됐다(정적도 박스가 서빙). 이미지·compose 배포는 `deploy-demo-onprem.yml`(workflow_dispatch — 콘솔 2종 포함 이미지 빌드→SSM Run Command 로 compose, ALPHA-542·554)가 한 번에 한다(전용 배포 역할 `deploy-role.tf` — `foundation` ECR 에 콘솔 UI 저장소 포함). 박스 `apply`(1회 인프라)와 `tenant_delivery` 발번(현재 수동 시드 — 발번기 후속)은 별도.
- **prod 환경**(`envs/prod`). (super-admin-ui 는 빌드 셸 스캐폴드됨(ALPHA-309) — 콘텐츠·기능은 ALPHA-288.)

> `data-pipeline` 시장 레인은 평일 15:40, 뉴스는 매일 00:10·08:10, 장중 수급은 평일 5슬롯으로 ENABLED다. **공시 `edge-dev-data-pipeline-disclosure`의 18:10 스케줄은 ALPHA-1068부터 DISABLED**이고 SFN 정의만 rollback 경로로 남는다. 공시는 `disclosure_minute/dart` 세션과 `disclosure-worker`가 소유한다. 같은 apply에서 `disclosure_schedule_state="DISABLED"`, `minute_session_disclosure_source_group="dart"`, stop `20:05`를 적용한다. ops catalog 4엔트리는 이 apply와 당일 분 레인 E2E를 확인한 뒤 별도 앱 PR에서 제거한다. 앱 이미지 CD와 terraform apply가 독립이라 catalog 제거를 같은 PR에 넣으면 이미지가 먼저 착지할 수 있기 때문이다. 거래일 07:45 이후 apply면 기존 가격 세션이 이미 DRAINED일 수 있으므로 `start-minute-session`을 쓰지 않는다. 예약 stop이 남은 20:05 전에는 `plan-minute-session --dataset disclosure_minute --source-group dart --session-date <오늘>`을 직접 실행한 뒤 `disclosure-worker`만 desired 1로 올린다. 이미 제출된 batch 실행은 먼저 완료시킨다. batch 완료나 apply가 20:05를 넘기면 오늘 세션을 만들지 않고 다음 거래일 07:45 자동 start를 기다린다. 늦게 만든 오늘 세션은 다음날 stop도 지목하지 못한다. rollback은 batch ENABLED, minute source group 빈 값, stop 16:10을 같은 apply로 되돌리고 기존 minute session이 드레인된 뒤 적용한다.
>
> **Reconciler(`edge-dev-data-pipeline-reconcile`)도 ENABLED**다. 첫 컷오버 PR 직후 ops 카탈로그에는 rollback용 공시 4작업이 잠시 남지만 `OPS_DISCLOSURE_SCHED_HHMM`이 빈 값이라 꺼진 18:10 슬롯을 결손으로 판정하지 않는다. 분 레인 E2E 뒤 catalog 정리 PR이 30작업을 26작업(시장 17 + 뉴스 6 + 장중 수급 3)으로 줄인다. 수동 슬롯은 `OPS_RUN_KEY`를 명시해 reconcile한다.
>
> **1분 세션 스케줄 3개**는 평일 start 07:45, stop 20:05, 업종지수 rollup 16:00 KST다. start는 가격·뉴스·공시·iNAV·업종지수 세션을 계획하고 세션 결속 서비스 9종을 올린다. 공시 격자는 universe와 무관하게 08:00–20:00이므로 stop은 마지막 window 뒤 20:05다. `analysis-consumer`는 이 목록 밖에서 SQS 잔여 기반 오토스케일링이 소유한다. stop은 phase DRAINED, 게이트 큐 0, outbox NEW 0을 연속 확인한 뒤 QC와 scale-down을 수행한다. ECS Task State Change rule은 start/stop 컨테이너의 비0 종료를 alarm SNS로 전달한다.
>
> ⚠️ **`kr_holidays`(envs/dev/main.tf)는 해마다 손으로 갱신해야 한다** — 거래소 캘린더 연동 전까지의 수동 주입 지점(ALPHA-387). 주말만 코드가 안다. 비면 **다섯 곳**이 함께 퇴화한다: Planner 가 평일 휴장일에 런을 계획하고, KRX 수집이 직전 거래일 PDF 를 휴장일 as-of 로 오라벨하며, **KIS iNAV 가드(ALPHA-557)가 그날을 거래일로 보고 직전 거래일 값을 오늘 것으로 적재**하고, **KIS 투자자 수집(ALPHA-562)이 그날을 거래일로 보고 풀리지 않을 OPSQ2001 블랙아웃을 심볼마다 75초씩 기다린다**(유니버스 전체면 ~10시간). **1분 세션 start(ALPHA-712)도 그날을 거래일로 보고 세션(가격·뉴스·iNAV)을 만들고 상주 서비스를 올린다** — window 는 전건 빈 캔들로 남는다. `planner`·`krx`·`kis`·`minute-session` task-def 가 같은 `OPS_KR_HOLIDAYS` 를 받는다.
>
> ⚠️ **US(FMP) 수집은 현재 꺼져 있다(`us_fmp_enabled=false`, ALPHA-558)** — 1분봉 백필이 공용 FMP 키 bandwidth(rolling 30일)를 소진해, 켜 두면 US 4잡(뉴스·가격·재무·ETF holdings)이 매 런 429 로 실패해 런을 FAILED 로 마감한다(KR 은 독립이라 무영향). bandwidth 회복 후 `true` 로 되돌리고 공백을 windowed 백필로 소급한다(소스별 복구성은 statemachine.tf `us_fmp_ingest_jobs` 주석).
