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
    ├── network/            # VPC, 3-tier 서브넷(public·private/compute·data/격리), IGW, NAT, AZ override
    ├── ecs-cluster/        # ECS 클러스터 + Service Connect + Fargate CP
    ├── ecs-service/        # 재사용 상시 서비스: task def + service + SG + IAM + 로그
    ├── alb/                # 공개 엣지 ALB (호스트 단위 1:1, mTLS verify 옵션 — ADR-0034. 호출자: sync·super-admin ALB)
    ├── rds/                # PostgreSQL(private·관리형 비밀번호)
    ├── schema-migrate/     # Flyway one-off task (ECR은 foundation 입력으로 decoupled)
    ├── github-oidc-deploy/ # GitHub Actions OIDC 배포 역할(최소 권한)
    ├── pipeline/           # 임시 news-pipeline Step Functions 배치 (self-contained)
    ├── data-pipeline/      # raw→normalize→feature→analyze Step Functions 배치 (data-pipeline·analysis-engine 이미지·S3 lake·시크릿·스케줄러)
    ├── static-site/        # S3(프라이빗)+CloudFront(OAC)+Route53 alias — 프론트 CDN
    └── demo-onprem/        # 가상 온프렘 데모 박스: EC2 + SG + IAM(SSM·ECR) + user-data(docker/compose 부트스트랩) — ADR-0033
```

`ecs-service` 를 super-admin-api·tenant-sync-api 가, `static-site` 를 tenant-console·super-admin UI 와 데모 MTS 페이지가 동일 재사용한다.
(tenant-console-api 는 onprem 플레인이라 dev ECS 에서 제거 — 실 배포처는 데모 박스 compose, ADR-0029·0033.)
두 API 는 각자 전용 ALB 뒤에 있다 — tenant-sync-api=`sync-dev.edgesignal.dev`(mTLS 예정), super-admin-api=`admin-api-dev.edgesignal.dev`. 진입점은 호스트 단위 1:1, 경로 라우팅 없음(ADR-0034).

## 설계 요지

- **단계 스택** — bootstrap(state) → foundation(zone·ECR·OIDC·ACM) → envs. env 는 foundation 자원을 이미지 URI·`data`(ACM/OIDC/ECR 조회)로 **느슨하게** 참조 → remote_state 강결합 없음. **apply 순서: foundation → env.**
- **와일드카드 ACM** — `*.edgesignal.dev` 을 리전당 1장(ALB=apne2, CloudFront=us-east-1). 새 서브도메인 추가 시 인증서 재발급 0.
- **네트워크 3-tier** — public(ALB·NAT) / private=compute(ECS, NAT 아웃바운드) / **data=RDS 격리(아웃바운드 없음)**. AZ `a·c`.
- **클러스터 분리** — 상시 API(`edge-dev-service`) / 배치(`edge-dev-worker`).
- **배치 = Step Functions** — 임시 news-pipeline 과 data-pipeline(raw→normalize→feature→analyze 4페이즈, 구 analysis-engine SFN 흡수 — ALPHA-408)을 분리해 `ecs:runTask.sync` 로 오케스트레이션(재시도·실패알림).
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
| **임시 파이프라인 스케줄러** | `DISABLED` (이미지·검증 전 자동실행 방지) | `pipeline` 모듈 `schedule_state = "ENABLED"` |
| **파이프라인 실패 알림 이메일** | ✅ 확인 완료 — 구독 활성(실측 2026-07-20, 구독 ARN 발급됨) | `pipeline_alarm_email` 기본값(변경 시 여기) |
| **super-admin ALB 보호** | 공개 도달 — WAF·IP 제한 미구현(콘솔 API 표면 노출 — tenants 는 이제 실 `tenant` DB, ALPHA-526). 앱 인증(AdminAuthFilter fail-closed)은 있으나 dev 시크릿 미배선으로 닫힘 | 앱 인증 본격화(ALPHA-474)·WAFv2(ALPHA-297)·`allowed_cidrs` 운영 판단 |
| **sync mTLS** | off — trust store 미주입(엔드포인트 공개 도달, dev 스텁·시드 데이터 전제) | CA·번들 준비(ALPHA-447) 후 `sync_mtls_trust_store_arn` 주입 |
| **오토스케일링** | 없음(`desired_count=1`) | 추후 |
| **NAT** | dev 단일 공유(`single_nat_gateway`) | prod 은 AZ당 1개 |

> ⚠️ `pipeline_alarm_email` 이 `null` 이면 SNS 구독 리소스가 `count=0` 으로 **아예 안 생겨** 실패
> 알림이 구독자 없는 토픽으로 사라진다 — "구독 없음"이 아니라 **알림 유실**이다. ALPHA-389 착수
> 전까지 실제로 그 상태였고(라이브 토픽 구독자 0), data-pipeline 정제가 run 스코프로 바뀐 뒤로는
> 실패 런의 raw 를 사람이 명시 재처리해야 하므로 이 알림이 그 절차의 유일한 트리거다.

### ⚪ 비어 있음 (off 아님 — 채워야 함, CD/수동 몫)

- 앱 ECR 이미지(push), 프론트 S3 콘텐츠 3개(build sync) — 백엔드(super-admin-api·tenant-sync-api)·data-pipeline·프론트 3종은 CD(`deploy-<app>.yml`·`deploy-data-pipeline.yml`·`deploy-<ui>.yml`)가 채운다. tenant-sync-api 최초 이미지는 `deploy-tenant-sync-api` 수동 실행(workflow_dispatch)으로 부트스트랩
- 파이프라인 이미지(`edge/pipeline:latest` placeholder) + 시크릿 fmp/openai(`REPLACE_ME` → 실제 키)

### 🔮 미구축 (후속 증분)

- **WAF**(ALPHA-297) — super-admin ALB(`admin-api-dev`)에 부착(그 ALB 에만 — sync 는 trust store 가 게이트). 선행이던 ALB 는 ALPHA-473 으로 도입됨.
- **데모 온프렘 런타임** — terraform(EC2·MTS 사이트)은 스캐폴드됨(ADR-0033). compose 스택·`deploy-demo-onprem.yml`(SSM Run Command)·CloudFront `/api/*`→EC2 오리진 프록시는 온프렘 코드(sync-agent·compliance) 완료 후(ALPHA-445).
- **prod 환경**(`envs/prod`). (super-admin-ui 는 빌드 셸 스캐폴드됨(ALPHA-309) — 콘텐츠·기능은 ALPHA-288.)

> `data-pipeline` 은 스케줄러 ENABLED — 평일 15:40 KST 자동 실행(컷오버, ALPHA-489). 구 `pipeline`(news) 은 DISABLED 라 수동. 애드혹·백필은 `aws stepfunctions start-execution` 으로.
