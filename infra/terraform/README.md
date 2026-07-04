# infra/terraform

`edge` 의 AWS 인프라를 코드로 정의한다. **완전 그린필드** — 도메인 등록(Route53 존·NS 위임)만 수동이고, 그 아래(SSL·네트워크·컴퓨트·데이터·CDN)는 전부 Terraform 이 소유한다. 다른 계정/region 에서도 `tfvars` 만 바꿔 재현된다.

> 배포 토폴로지 결정은 [docs/adr/0009](../../docs/adr/0009-aws-deployment-topology.md), 신뢰 경계는 [docs/architecture.md](../../docs/architecture.md) §4.

## 구조 — 단계(phase) 스택 + 모듈

수명·blast-radius 로 3단계 스택을 나눈다. 각 스택은 **독립 state·독립 apply**.

```
infra/terraform/
├── bootstrap/          # 원격 state 그릇(S3 버킷 + 네이티브 락). 자체 state=로컬, 계정당 1회
├── foundation/         # 계정 전역·장수명: Route53 존 · 와일드카드 ACM ×2(apne2·us-east-1) · ECR ×6 · GitHub OIDC provider
├── envs/dev/           # 환경: 모듈을 엮음. 구체값은 terraform.tfvars
└── modules/
    ├── network/            # VPC, 3-tier 서브넷(public·private/compute·data/격리), IGW, NAT, AZ override
    ├── ecs-cluster/        # ECS 클러스터 + Service Connect + Fargate CP
    ├── ecs-service/        # 재사용 상시 서비스: task def + service + SG + IAM + 로그
    ├── alb/                # 공개 엣지 ALB (임시 검증 → gateway 증분서 교체)
    ├── rds/                # PostgreSQL(private·관리형 비밀번호)
    ├── schema-migrate/     # Flyway one-off task (ECR은 foundation 입력으로 decoupled)
    ├── github-oidc-deploy/ # GitHub Actions OIDC 배포 역할(최소 권한)
    ├── pipeline/           # Step Functions 배치 (self-contained: SFN·태스크2종·S3·시크릿·스케줄러)
    └── static-site/        # S3(프라이빗)+CloudFront(OAC)+Route53 alias — 프론트 CDN
```

`ecs-service` 를 widget-api·tenant-console-api·super-admin-api·gateway 가, `static-site` 를 widget·tenant-console·super-admin UI 가 동일 재사용한다.

## 설계 요지

- **단계 스택** — bootstrap(state) → foundation(zone·ECR·OIDC·ACM) → envs. env 는 foundation 자원을 이미지 URI·`data`(ACM/OIDC/ECR 조회)로 **느슨하게** 참조 → remote_state 강결합 없음. **apply 순서: foundation → env.**
- **와일드카드 ACM** — `*.edgesignal.dev` 을 리전당 1장(ALB=apne2, CloudFront=us-east-1). 새 서브도메인 추가 시 인증서 재발급 0.
- **네트워크 3-tier** — public(ALB·NAT) / private=compute(ECS, NAT 아웃바운드) / **data=RDS 격리(아웃바운드 없음)**. AZ `a·c`.
- **클러스터 분리** — 상시 API(`edge-dev-service`) / 배치(`edge-dev-worker`).
- **배치 = Step Functions** — 수집→분석 순차 스텝을 `ecs:runTask.sync` 로 오케스트레이션(순서·재시도·실패알림).
- **비밀번호는 코드/state 에 없음** — RDS 관리형 시크릿, 외부 키는 Secrets Manager(값 수동 주입).

## 사용

```bash
# 최초 1회 — 원격 state 그릇
cd bootstrap && terraform apply

# 순서 엄수: foundation 먼저(ACM·ECR·OIDC), 그다음 env
cd ../foundation && terraform apply
cd ../envs/dev  && terraform apply
```

- 상태는 **S3 원격**(`edge-tfstate-393229433969`, 네이티브 락). backend 는 `foundation/backend.tf`·`envs/dev/backend.tf`.
- env 를 foundation 전에 돌리면 `data` 소스에서 실패한다 — 그게 순서를 강제하는 안전장치.
- 이미지 태그: `terraform.tfvars` 의 `*_image` 가 TF 소유 baseline. 앱 CD(`deploy-<app>.yml`)가 semver 태그를 올린다.
  서비스의 실행 task 정의는 CD 소유라 TF 가 되돌리지 않는다(`ecs-service` 의 `ignore_changes = [task_definition]`);
  `terraform.tfvars` 핀은 신규 생성 시 baseline 으로만 쓰인다.

## 현재 상태 (2026-07-04)

인프라는 **구조 완성 + apply 됨**. 다만 아래는 의도적으로 꺼두었거나 비어 있다.

### 🔴 의도적 off (준비되면 켠다)

| 기능 | 상태 | 켜는 법 |
|------|------|---------|
| **파이프라인 스케줄러** | `DISABLED` (이미지·검증 전 자동실행 방지) | 모듈 `schedule_state = "ENABLED"` |
| **파이프라인 실패 알림 이메일** | 구독 없음(토픽만) | `pipeline_alarm_email = "..."` |
| **내부 API**(tenant-console·super-admin) | idle — ALB 타깃 없음, Service Connect 만 | gateway 도입 시 연결 |
| **widget-api DB 연동** | TF 주입되나 앱 미사용(`application.yaml` DataSource exclude) | 앱에서 exclude 제거 |
| **오토스케일링** | 없음(`desired_count=1`) | 추후 |
| **NAT** | dev 단일 공유(`single_nat_gateway`) | prod 은 AZ당 1개 |

### ⚪ 비어 있음 (off 아님 — 채워야 함, CD/수동 몫)

- 앱 ECR 이미지 6개(push), 프론트 S3 콘텐츠 3개(build sync)
- 파이프라인 이미지(`edge/pipeline:latest` placeholder) + 시크릿 fmp/openai(`REPLACE_ME` → 실제 키)

### 🔮 미구축 (후속 증분)

- **gateway**(단일 엣지) — internal-only ECS 서비스로 스테이징됨(ALPHA-296, Service Connect `gateway:8080`).
  공개 ALB 타깃 컷오버(지금 ALB 는 widget-api 임시 대역)와 리버스 프록시 라우팅은 ALPHA-294.
- **WAF** — gateway 증분에서.
- **prod 환경**(`envs/prod`). (super-admin-ui 는 빌드 셸 스캐폴드됨(ALPHA-309) — 콘텐츠·기능은 ALPHA-288.)

> 배치 파이프라인은 스케줄러 DISABLED 라 자동 실행 안 됨. 수동 검증은 `aws stepfunctions start-execution` 으로.
