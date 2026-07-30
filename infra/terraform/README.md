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
│   └── demo-onprem/    # 가상 온프렘 데모 스택 (EC2 + MTS 사이트, dev와 별도 state — ADR-0033)
└── modules/
    ├── network/            # VPC, 3-tier 서브넷(public·private/compute·data/격리), IGW, NAT, S3 gateway endpoint(private만), AZ override
    ├── ecs-cluster/        # ECS 클러스터 + Service Connect + Fargate CP
    ├── ecs-service/        # 재사용 상시 서비스: task def + service + SG + IAM + 로그
    ├── alb/                # 공개 엣지 ALB (호스트 단위 1:1, mTLS verify 옵션 — ADR-0034. 호출자: sync·super-admin ALB)
    ├── rds/                # PostgreSQL(private·관리형 비밀번호)
    ├── schema-migrate/     # Flyway one-off task (ECR은 foundation 입력으로 decoupled)
    ├── github-oidc-deploy/ # GitHub Actions OIDC 배포 역할(최소 권한)
    ├── pipeline/           # 구 news-pipeline SFN 의 존치 자원 — data-pipeline 이 쓰는 lake S3 버킷만 소유 (ALPHA-549)
    ├── data-pipeline/      # 시장 SFN(raw→normalize→feature→analyze) + 뉴스 SFN(ALPHA-553 지식 레인 분리) Step Functions 배치 (data-pipeline·analysis-engine 이미지·S3 lake·시크릿·스케줄러)
    ├── static-site/        # S3(프라이빗)+CloudFront(OAC)+Route53 alias — 프론트 CDN
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
- **배치 = Step Functions** — data-pipeline(raw→normalize→feature→analyze 4페이즈, 구 analysis-engine SFN 흡수 — ALPHA-408)을 `ecs:runTask.sync` 로 오케스트레이션(재시도·실패알림). 구 임시 news-pipeline SFN 은 ALPHA-549 에서 제거 — `pipeline` 모듈은 lake 버킷만 존치.
- **비밀번호는 코드/state 에 없음** — RDS 관리형 시크릿, 외부 키는 Secrets Manager(값 수동 주입).

## 사용

```bash
# 최초 1회 — 원격 state 그릇
cd bootstrap && terraform apply

# 순서 엄수: foundation 먼저(ACM·ECR·OIDC), 그다음 env
cd ../foundation && terraform apply
cd ../envs/dev  && terraform apply
```

- **envs/dev 는 Terraform CD 로 배포된다(ALPHA-311)**: `envs/dev/**`·`modules/**` 를 바꾼 PR 이 `terraform-plan.yml`(read-only 역할 `edge-tf-plan`)로 plan 을 PR 코멘트에 게시하고, dev 머지 시 `terraform-apply.yml`(`edge-tf-apply`, trust=`ref:refs/heads/dev`)이 apply 한다. 두 역할은 foundation `tf-cd.tf` 소유. 위 수동 apply 는 **bootstrap·foundation**(CD 대상 아님) 및 env 브레이크글래스용이다.
- **envs/demo-onprem** 은 apply CD 밖이다(ADR-0033) — PR 에서 오프라인 `terraform validate`(전용 `terraform-validate-demo.yml`, creds 불필요)로만 검증하고, apply 는 수동. dev plan(`terraform-plan.yml`, OIDC creds)과 분리해 데모만 바꾼 PR 이 dev 자격/drift 에 묶이지 않게 한다. 데모 런타임(compose·sync·CloudFront API 오리진)은 온프렘 코드 완료 후 후속(ALPHA-445).
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
| **파이프라인 실패 알림 이메일** | ✅ 확인 완료 — 구독 활성(실측 2026-07-20, 구독 ARN 발급됨) | `pipeline_alarm_email` 기본값(변경 시 여기) |
| **super-admin ALB 보호** | WAFv2 부착됨(ALPHA-297 — AWS Managed CommonRuleSet·KnownBadInputs, 차단 동작·CloudWatch 메트릭). IP 제한은 미적용(콘솔 API 표면 노출 — tenants 는 이제 실 `tenant` DB, ALPHA-526). 앱 인증(AdminAuthFilter fail-closed)은 있으나 dev 시크릿 미배선으로 닫힘 | 앱 인증 본격화(ALPHA-474)·`allowed_cidrs` 운영 판단·커스텀 룰/레이트리밋 후속 |
| **sync mTLS** | off — trust store 미주입(엔드포인트 공개 도달, dev 스텁·시드 데이터 전제) | CA·번들 준비(ALPHA-447) 후 `sync_mtls_trust_store_arn` 주입 |
| **오토스케일링** | 없음(`desired_count=1`) | 추후 |
| **NAT** | dev 단일 공유(`single_nat_gateway`) | prod 은 AZ당 1개 |

> ⚠️ `pipeline_alarm_email` 이 `null` 이면 SNS 구독 리소스가 `count=0` 으로 **아예 안 생겨** 실패
> 알림이 구독자 없는 토픽으로 사라진다 — "구독 없음"이 아니라 **알림 유실**이다. ALPHA-389 착수
> 전까지 실제로 그 상태였고(라이브 토픽 구독자 0), data-pipeline 정제가 run 스코프로 바뀐 뒤로는
> 실패 런의 raw 를 사람이 명시 재처리해야 하므로 이 알림이 그 절차의 유일한 트리거다.

### ⚪ 비어 있음 (off 아님 — 채워야 함, CD/수동 몫)

- 앱 ECR 이미지(push), 프론트 S3 콘텐츠 3개(build sync) — 백엔드(super-admin-api·tenant-sync-api)·data-pipeline·프론트 3종은 CD(`deploy-<app>.yml`·`deploy-data-pipeline.yml`·`deploy-<ui>.yml`)가 채운다. tenant-sync-api 최초 이미지는 `deploy-tenant-sync-api` 수동 실행(workflow_dispatch)으로 부트스트랩

### 🔮 미구축 (후속 증분)

- **데모 온프렘 런타임** — terraform(EC2·MTS 사이트)은 스캐폴드됨(ADR-0033), 온프렘 박스 compose 는 `demo/onprem/docker-compose.yml`(ALPHA-444 — 고객경로 7서비스 + 검수 콘솔 co-host 2(tenant-console-api·nginx `tenant-console-ui`, ALPHA-554), ECR 이미지 참조, sync-agent→실 cloud). 데모 서빙(ALPHA-632)은 `proxy-site` 모듈 2개 인스턴스 — MTS(`demo-mts.edgesignal.dev` → 박스 `:8080` mock-broker, 정적은 이미지 내장)·검수 콘솔(`demo-console.edgesignal.dev` → 박스 `:8090` nginx)이며, 콘솔 진입은 로그인 화면(ALPHA-626)이 게이트한다 — SSM 터널은 비상 경로(ALPHA-627, 구 127.0.0.1 전용 바인딩 폐기). 구 MTS S3 버킷·sync 갈래는 제거됐다(정적도 박스가 서빙). 이미지·compose 배포는 `deploy-demo-onprem.yml`(workflow_dispatch — 콘솔 2종 포함 이미지 빌드→SSM Run Command 로 compose, ALPHA-542·554)가 한 번에 한다(전용 배포 역할 `deploy-role.tf` — `foundation` ECR 에 콘솔 UI 저장소 포함). 박스 `apply`(1회 인프라)와 `tenant_delivery` 발번(현재 수동 시드 — 발번기 후속)은 별도.
- **prod 환경**(`envs/prod`). (super-admin-ui 는 빌드 셸 스캐폴드됨(ALPHA-309) — 콘텐츠·기능은 ALPHA-288.)

> `data-pipeline`(시장 `edge-dev-data-pipeline`) 은 스케줄러 ENABLED — 평일 15:40 KST 자동 실행(컷오버, ALPHA-489). **뉴스 `edge-dev-data-pipeline-news`(ALPHA-553)** 도 평일 15:00·15:30·23:50 KST 스케줄러 **ENABLED**(PR2 컷오버, 2026-07-27) — 두 스케줄 모두 SFN 직접 시작이 아니라 **ops task-def 의 `plan-run`(Planner) 경유**다(뉴스는 `OPS_PIPELINE_TYPE=news`, ALPHA-591). 구 `pipeline`(news) 은 DISABLED 라 수동. 애드혹·백필도 **`plan-run`** 으로 — 슬롯 키가 분 단위라(ALPHA-564) 그 실행이 자기 슬롯으로 원장에 남아 관측된다. `aws stepfunctions start-execution` 을 직접 쓰면 원장에 안 남아 대조 대상이 아니다. ⚠️ `schedule_expression`·`news_schedule_expressions` 의 cron HH:MM 이 곧 Reconciler 의 슬롯 기준이다(`OPS_DAILY_SCHED_HHMM`·`OPS_NEWS_SCHED_HHMM` 은 별도 변수가 아니라 여기서 파생) — cron 을 바꾸면 슬롯 기준도 같이 움직인다. `schedule_timezone` 은 `Asia/Seoul` 고정(validation).
>
> **Reconciler(`edge-dev-data-pipeline-reconcile`) 도 ENABLED** — `rate(15 minutes)`(컷오버, ALPHA-588). 이게 꺼져 있으면 `orchestration_status` 가 영영 NULL 이고, 실행되지 않은 작업(MISSED)·기동 실패(LAUNCH_UNCONFIRMED)·계획 결측(PLANNER_MISSING)이 아무도 판정하지 않아 원장에 안 뜬다. ⚠️ **등록 27작업은 이제 전부 자기 원장을 직접 쓴다**(ALPHA-596 이 `krx`·`dart`, ALPHA-610 이 `deepseek` task-def 에 DB env 를 주며 `instrumented=False` 가 0개가 됐다 — 작업 수 21→27 은 ALPHA-591 뉴스 레인 편입). 그래서 Reconciler 는 더는 "성공한 작업을 PENDING 에서 꺼내는" 유일 경로가 아니라 **백스톱**이다 — attempt 가 비어 있으면 그건 정상이 아니라 `LEDGER_GAP` 이다. ⚠️ 주기 실행은 **스케줄 슬롯 하나만** 대조한다 — 수동 슬롯(`plan-run` 으로 만든 것)은 `OPS_RUN_KEY` 를 명시해 돌려야 대조된다(ALPHA-565 가 해소 예정).
>
> ⚠️ **`kr_holidays`(envs/dev/main.tf)는 해마다 손으로 갱신해야 한다** — 거래소 캘린더 연동 전까지의 수동 주입 지점(ALPHA-387). 주말만 코드가 안다. 비면 **네 곳**이 함께 퇴화한다: Planner 가 평일 휴장일에 런을 계획하고, KRX 수집이 직전 거래일 PDF 를 휴장일 as-of 로 오라벨하며, **KIS iNAV 가드(ALPHA-557)가 그날을 거래일로 보고 직전 거래일 값을 오늘 것으로 적재**하고, **KIS 투자자 수집(ALPHA-562)이 그날을 거래일로 보고 풀리지 않을 OPSQ2001 블랙아웃을 심볼마다 75초씩 기다린다**(유니버스 전체면 ~10시간). `planner`·`krx`·`kis` task-def 가 같은 `OPS_KR_HOLIDAYS` 를 받는다.
>
> ⚠️ **US(FMP) 수집은 현재 꺼져 있다(`us_fmp_enabled=false`, ALPHA-558)** — 1분봉 백필이 공용 FMP 키 bandwidth(rolling 30일)를 소진해, 켜 두면 US 4잡(뉴스·가격·재무·ETF holdings)이 매 런 429 로 실패해 런을 FAILED 로 마감한다(KR 은 독립이라 무영향). bandwidth 회복 후 `true` 로 되돌리고 공백을 windowed 백필로 소급한다(소스별 복구성은 statemachine.tf `us_fmp_ingest_jobs` 주석).
