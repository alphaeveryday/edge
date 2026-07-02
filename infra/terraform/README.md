# infra/terraform

`edge` 의 AWS 인프라를 코드로 정의한다. **완전 그린필드** — 기존 click-ops 자원을 참조(`data`)하지 않고 VPC부터 전부 새로 만든다. 그래서 다른 계정/region 에서도 `tfvars` 만 바꿔 그대로 재현된다.

> 범위 결정 배경은 [docs/architecture.md](../../docs/architecture.md) §4(신뢰 경계)·§6(배포 토폴로지). 인프라가 확정되면 ADR 로 증류한다.

## 구조

```
infra/terraform/
├── modules/
│   ├── network/            # VPC, public/private 서브넷(2 AZ), IGW, NAT
│   ├── ecs-cluster/        # ECS 클러스터 + Service Connect 네임스페이스 + Fargate CP
│   ├── ecs-service/        # 재사용 서비스 모듈: task def + service + SG + IAM + 로그
│   ├── alb/                # 공개 엣지 ALB (HTTPS, ACM 인증서)
│   ├── rds/                # PostgreSQL (private, RDS 관리형 시크릿)
│   ├── schema-migrate/     # 스키마 마이그레이션 one-off task (Flyway)
│   ├── github-oidc-deploy/ # GitHub Actions OIDC 배포 역할
│   └── sfn-pipeline/       # 배치 워커: SFN + ECS one-off task 체인 + Scheduler
└── envs/
    └── dev/                # 모듈을 엮는 환경. 구체값은 terraform.tfvars
```

`ecs-service` 모듈 하나를 widget-api·gateway·tenant-console-api·super-admin-api 가 **동일하게 재사용**한다. 서비스 간 차이(이미지·자원·인바운드 허용자)는 변수로만 표현한다.

## 설계 요지

- **gateway 만 공개 엣지** — 나머지 API 는 private 서브넷, 인터넷 facing LB 없음.
- **SG 백스톱** — 각 서비스 인바운드를 허용 SG(gateway)로 좁힌다. 엣지 오설정이 단일 실패점이 되지 않게.
- **Service Connect** — gateway→API 내부 호출은 `http://<service>:<port>` 로. internal ALB 불필요.
- **클러스터 분리** — 상시 API(`edge-dev-service`)와 배치 워커(`edge-dev-worker`)를 나눈다. 클러스터는 무료라 cost-neutral. 워커는 상시 서비스가 아니라 EventBridge Scheduler→Step Functions 가 단계마다 Fargate task 를 띄웠다 내리는 구조(`sfn-pipeline` 모듈)로, NAT 대신 퍼블릭 서브넷+퍼블릭 IP 로 아웃바운드한다(비용 회피).

## 사용

```bash
cd envs/dev
terraform init      # 첫 실행: 프로바이더 설치
terraform plan      # 변경 미리보기
terraform apply     # 실제 생성
```

- 상태는 기본 **로컬**. 공유하려면 [`backend.tf`](envs/dev/backend.tf) 주석을 풀고 S3+DynamoDB 로 전환(부트스트랩 필요).
- 이미지 태그를 올릴 때는 `terraform.tfvars` 의 `widget_api_image` 갱신 후 `apply`.

## 현재 범위와 의도적 보류

**정의된 것**: VPC/서브넷/NAT, service 클러스터, ECS 서비스 3종(widget-api — 임시로 공개 ALB 뒤, tenant-console-api·super-admin-api — internal 대기), 공개 엣지 ALB+ACM+Route53, RDS(PostgreSQL, private), 스키마 마이그레이션 one-off task + GitHub OIDC 배포 역할, news-pipeline 워커(`edge-dev-worker` — SFN 8단계·태스크 정의 2종·Scheduler·S3 raw/curated, **apply 전·스케줄 DISABLED**).

**아직 안 만든 것(후속 증분에서)**:
- **gateway 서비스 + ALB 타깃 교체 + WAF** — gateway 증분에서. 그때 widget-api 는 다시 private 으로 닫는다.
- **widget-api DataSource 재활성화** — RDS·시크릿 주입 배선은 끝났으나, `application.yaml` 의 autoconfigure.exclude 제거는 테스트 컨텍스트가 실DB 를 요구하게 되어 별도 처리. 지금은 주입만 되고 앱은 미사용.
- **news-pipeline 컷오버** — 워커 apply → 데이터 이관(pg_dump·s3 sync) → 구 CDK Scheduler 비활성·신 스케줄 활성 → CDK 스택 삭제. 원격 상태 전환은 데이터 이관 직전이 데드라인.
- **컨테이너 헬스체크** — 현재 런타임 이미지에 curl 이 없어 미설정. ALB target group(gateway 증분) 또는 이미지에 curl 추가로 활성.
- **오토스케일링·원격 상태 백엔드**.

> widget-api 는 내부 서비스라 지금은 외부에서 직접 도달할 수 없다. 배포 성공은 ECS 서비스가 RUNNING 으로 안정화되는지로 확인한다(헬스 200 은 로컬에서 이미 검증).
