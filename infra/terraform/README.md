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
    ├── rds/                # PostgreSQL(private·관리형 비밀번호) + 관측(FreeableMemory 경보·Performance Insights — 경보는 data-pipeline 토픽으로, ALPHA-919)
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

- **데모 온프렘 런타임** — terraform(EC2·MTS 사이트)은 스캐폴드됨(ADR-0033), 온프렘 박스 compose 는 `demo/onprem/docker-compose.yml`(ALPHA-444 — 고객경로 7서비스 + 검수 콘솔 co-host 2(tenant-console-api·nginx `tenant-console-ui`, ALPHA-554), ECR 이미지 참조, sync-agent→실 cloud). 데모 서빙(ALPHA-632)은 `proxy-site` 모듈 2개 인스턴스 — MTS(`demo-mts.edgesignal.dev` → 박스 `:8080` mock-broker, 정적은 이미지 내장)·검수 콘솔(`demo-console.edgesignal.dev` → 박스 `:8090` nginx)이며, 콘솔 진입은 로그인 화면(ALPHA-626)이 게이트한다 — SSM 터널은 비상 경로(ALPHA-627, 구 127.0.0.1 전용 바인딩 폐기). 구 MTS S3 버킷·sync 갈래는 제거됐다(정적도 박스가 서빙). 이미지·compose 배포는 `deploy-demo-onprem.yml`(workflow_dispatch — 콘솔 2종 포함 이미지 빌드→SSM Run Command 로 compose, ALPHA-542·554)가 한 번에 한다(전용 배포 역할 `deploy-role.tf` — `foundation` ECR 에 콘솔 UI 저장소 포함). 박스 `apply`(1회 인프라)와 `tenant_delivery` 발번(현재 수동 시드 — 발번기 후속)은 별도.
- **prod 환경**(`envs/prod`). (super-admin-ui 는 빌드 셸 스캐폴드됨(ALPHA-309) — 콘텐츠·기능은 ALPHA-288.)

> `data-pipeline`(시장 `edge-dev-data-pipeline`) 은 스케줄러 ENABLED — 평일 15:40 KST 자동 실행(컷오버, ALPHA-489). **뉴스 `edge-dev-data-pipeline-news`(ALPHA-553)** 도 00:10·08:10 KST 스케줄러 **ENABLED**(PR2 컷오버, 2026-07-27) — **주 7일**이다(ALPHA-874: 수집 창이 `[어제, 오늘]` 2일이라 평일 크론에선 토요일만 어느 런에도 안 잡혔다. 일요일은 월요일 런이 덮는다). 슬롯이 3개(15:00·15:30·23:50)에서 2개로 바뀐 것은 ALPHA-893 이고, 남은 day-close 를 23:50 → 00:10 으로 옮긴 것은 ALPHA-905 다(창이 `[어제, 오늘]` 2일인데 00:10 은 '오늘'이 10분뿐이라 긁는 양이 절반 — ~124p → ~62p. 덤으로 자정 crossing 이 사라지고 하루가 통째로 닫힌다). 오후 두 슬롯이 내려간 것은 EOD 가격 설명 폐기로 그 소비자(15:40 analyze)가 사라졌기 때문이고, 08:10 은 밤새 유입분을 배치 코퍼스로 확정한다 — **분 레인이 못 닿는 꼬리를 담는 것**이 목적이다(09:00 첫 poll 은 anchor 없는 seed poll 이라 최신 400건까지만 집고, 이후 poll 은 머리를 쫓아 꼬리로 안 내려간다. 배치 창은 2일·상한 160 page). ⚠️ 09:00 전에 끝나야 하는 이유는 그것과 **별개**로 **벤더 경합**이다 — 두 레인이 같은 BigKinds 를 같은 IP 로 치고 차단은 재시도가 연장한다(ALPHA-645). ⚠️ 분 레인의 첫 poll 페이지 수를 줄여 주지는 않는다 — 원장이 분리돼 있다(LLM 장부도 마찬가지 — ALPHA-900). 커버리지는 슬롯 수가 아니라 2일 창이 정한다 — 날 X 는 X+1일 00:10 과 X+1일 08:10 이 이중으로 덮는다 — 두 스케줄 모두 SFN 직접 시작이 아니라 **ops task-def 의 `plan-run`(Planner) 경유**다(뉴스는 `OPS_PIPELINE_TYPE=news`, ALPHA-591). 구 `pipeline`(news) 은 DISABLED 라 수동. 애드혹·백필도 **`plan-run`** 으로 — 슬롯 키가 분 단위라(ALPHA-564) 그 실행이 자기 슬롯으로 원장에 남아 관측된다. `aws stepfunctions start-execution` 을 직접 쓰면 원장에 안 남아 대조 대상이 아니다. **공시 `edge-dev-data-pipeline-disclosure`(ALPHA-722·724)** 스케줄러는 **DISABLED** 다 — 공시는 SFN 레인을 떠나 **1분 세션**이 소유한다(ALPHA-875, 상주 `disclosure-worker`. 한 window 가 collect→normalize×2→load 체인 전체다). 15:40 런에서도, 이 레인에서도 공시를 찾지 마라 — 원장은 `minute_ingestion_window` 다. SFN 정의·task-def·ARN 주입은 **롤백 경로로 남겼고**, 되살리려면 세 값이 **같은 apply** 여야 한다: `disclosure_schedule_state="ENABLED"` · `minute_session_disclosure_source_group=""` · `ops/catalog.py` 4엔트리 복원(②를 빠뜨리면 두 레인이 같은 창을 긁어 DART 일 한도를 태운다 — 조용한 쪽이다). **장중 수급 `edge-dev-data-pipeline-investor-intraday`(ALPHA-769)** 도 평일 5슬롯(09:35·10:05·11:25·13:25·14:35 KST) 스케줄러 **ENABLED**(`OPS_PIPELINE_TYPE=investor-intraday`) — 앞 두 레인과 달리 **컷오버가 아니라 신설**이라 DISABLED 신설 후 별도 apply 를 밟지 않았다(이 3스텝은 시장 SFN 이 돈 적이 없어 겹침 창이 없다). 슬롯 수는 벤더 갱신 시각(하루 4~5회)의 합집합이 정한다. ⚠️ `schedule_expression`·`news_schedule_expressions`·`disclosure_schedule_expressions`·`investor_intraday_schedule_expressions` 의 cron HH:MM 이 곧 Reconciler 의 슬롯 기준이다(`OPS_DAILY_SCHED_HHMM`·`OPS_NEWS_SCHED_HHMM`·`OPS_DISCLOSURE_SCHED_HHMM`·`OPS_INVESTOR_INTRADAY_SCHED_HHMM` 은 별도 변수가 아니라 여기서 파생) — cron 을 바꾸면 슬롯 기준도 같이 움직인다. **같은 cron 의 일·요일 필드에서는 `OPS_*_SCHED_WEEKEND` 가 파생된다**(ALPHA-874) — 시각만으로는 그 슬롯이 주말에도 예정된 것인지 알 수 없어, 없으면 Reconciler 가 레인 구분 없이 주말을 통째로 건너뛴다(= 주 7일 레인의 주말 결측 탐지가 0). 표기는 화이트리스트라 `MON-FRI`·`* * ?`·`? * *` 외의 조합과 한 레인 안의 표기 혼합은 **plan 단계에서 죽는다** — 배포된 뒤 닫히지 않는 이슈를 여느니 apply 전에 멈춘다. 단 **공시·장중 수급 슬롯 기준은 스케줄이 ENABLED 일 때만 주입된다**(장중 수급만 켜져 있어 주입되고, **공시는 꺼져 있어 빈 값이다** — 그래서 공시 슬롯은 판정 대상에서 통째로 빠진다. ⚠️ 그 대가로 컷오버 apply 시점에 in-flight 였던 공시 런은 다시 대조되지 않고, 그때 열려 있던 공시 `PLANNER_MISSING` 은 해소 경로가 없어 OPEN 으로 남는다 — 09:00~18:40 KST 밖에서 apply 하거나, 이후 그날 슬롯을 `OPS_RUN_KEY` 로 지목해 `reconcile` 을 한 번 돌린다): 꺼진 채 넣으면 Reconciler 가 뜰 리 없는 슬롯을 결측으로 판정해 참인 `PLANNER_MISSING` 을 **그날 지난 슬롯마다 하나씩** 연다(공시 하루 최대 10개, 장중 수급 5개 — 같은 슬롯 재탐지는 `dedupe_key` OPEN 부분 유니크로 `occurrence_count` 만 올리므로 이슈가 시간당 불어나진 않는다. 해소 경로는 있다 — 그 슬롯의 런이 뒤늦게라도 생기면 `detect_planner_missing` 이 `run_present` 로 RESOLVED 한다. 스케줄을 끈 채 슬롯 기준만 남긴 경우가 문제인 것이다: 뜰 런이 없으니 계속 OPEN 이다). ⚠️ **`OPS_*_SCHED_WEEKEND` 형제에는 그 ENABLED 조건이 없다** — 슬롯 기준(HH:MM)이 빈 값이면 `entry._due_slots` 가 그 레인을 **먼저** 통째로 건너뛰므로, 요일 플래그만 남아도 아무것도 기대되지 않는다. `schedule_timezone` 은 `Asia/Seoul` 고정(validation).
>
> **Reconciler(`edge-dev-data-pipeline-reconcile`) 도 ENABLED** — `rate(15 minutes)`(컷오버, ALPHA-588). 이게 꺼져 있으면 `orchestration_status` 가 영영 NULL 이고, 실행되지 않은 작업(MISSED)·기동 실패(LAUNCH_UNCONFIRMED)·계획 결측(PLANNER_MISSING)이 아무도 판정하지 않아 원장에 안 뜬다. ⚠️ **등록 26작업은 이제 전부 자기 원장을 직접 쓴다**(30→26 은 ALPHA-875 가 공시 4작업을 1분 레인으로 보낸 몫이다)(ALPHA-596 이 `krx`·`dart`, ALPHA-610 이 `deepseek` task-def 에 DB env 를 주며 `instrumented=False` 가 0개가 됐다 — 작업 수 21→27 은 ALPHA-591 뉴스 레인 편입, 27→30 은 ALPHA-769 장중 수급 3작업 신설). 그래서 Reconciler 는 더는 "성공한 작업을 PENDING 에서 꺼내는" 유일 경로가 아니라 **백스톱**이다 — attempt 가 비어 있으면 그건 정상이 아니라 `LEDGER_GAP` 이다. ⚠️ 주기 실행이 대조하는 것은 **스케줄 슬롯**뿐이다 — 레인별로 그날 지난 슬롯을 전부 물지만(`_due_slots`, ALPHA-591 이 "최신 하나만" 에서 넓혔다: 뉴스·공시·장중 수급은 다슬롯이라 최신만 보면 앞 슬롯이 영영 대조되지 않는다), 스케줄 시각과 어긋나는 수동 슬롯(`plan-run` 이 분 단위로 만든 것)은 `OPS_RUN_KEY` 를 명시해 돌려야 대조된다(ALPHA-565 가 해소 예정).
>
> **1분 세션 스케줄 2개(`edge-dev-data-pipeline-minute-session-start`·`-stop`) 도 ENABLED** — 평일 07:45·20:05 KST(컷오버, ALPHA-712). 상주 서비스 **9종**(가격 3 + 뉴스 소비자 2 + news-worker + disclosure-worker + inav-worker + sector-index-worker, ALPHA-713·717·875·882·887)의 `desired_count` 를 세션 수명에 맞춰 올리고 내린다(terraform 은 그 값을 `ignore_changes` 로 뒀다). ⚠️ **`analysis-consumer`(ALPHA-719)는 이 목록에서 빠진다**(ALPHA-912 컷오버 완료) — desired 를 SQS 잔여 일감 오토스케일링이 소유하고 세션은 안 건드린다. 세션 코드가 공용 목록에서 그 이름을 빼며, 뺄 근거인 env 가 비면 fail-loud 다. cron 두 개는 **universe 가 정하는 세션 범위 밖**이어야 한다 — 시간외 종목이 하나라도 있으면 계획 범위가 08:00–20:00 이고 없으면 09:00–15:30 이다(좁히면 개장 뒤 기동·마감 전 종료). 내리는 조건은 시각이 아니라 **원장 상태**다(phase DRAINED → 게이트 큐 깊이 0 → 미발행 outbox NEW 0, 연속 확인). ⚠️ 스케줄러는 RunTask **제출**까지만 보므로 컨테이너 exit≠0 은 관측되지 않는다 — daily 레인의 Reconciler 같은 백스톱이 이 레인엔 아직 없어, start 가 그렇게 실패하면 그날은 통째로 안 돈다(신호는 컨테이너 로그와 `desired_count` 뿐).
>
> ⚠️ **`kr_holidays`(envs/dev/main.tf)는 해마다 손으로 갱신해야 한다** — 거래소 캘린더 연동 전까지의 수동 주입 지점(ALPHA-387). 주말만 코드가 안다. 비면 **다섯 곳**이 함께 퇴화한다: Planner 가 평일 휴장일에 런을 계획하고, KRX 수집이 직전 거래일 PDF 를 휴장일 as-of 로 오라벨하며, **KIS iNAV 가드(ALPHA-557)가 그날을 거래일로 보고 직전 거래일 값을 오늘 것으로 적재**하고, **KIS 투자자 수집(ALPHA-562)이 그날을 거래일로 보고 풀리지 않을 OPSQ2001 블랙아웃을 심볼마다 75초씩 기다린다**(유니버스 전체면 ~10시간). **1분 세션 start(ALPHA-712)도 그날을 거래일로 보고 세션(가격·뉴스·iNAV)을 만들고 상주 서비스를 올린다** — window 는 전건 빈 캔들로 남는다. `planner`·`krx`·`kis`·`minute-session` task-def 가 같은 `OPS_KR_HOLIDAYS` 를 받는다.
>
> ⚠️ **US(FMP) 수집은 현재 꺼져 있다(`us_fmp_enabled=false`, ALPHA-558)** — 1분봉 백필이 공용 FMP 키 bandwidth(rolling 30일)를 소진해, 켜 두면 US 4잡(뉴스·가격·재무·ETF holdings)이 매 런 429 로 실패해 런을 FAILED 로 마감한다(KR 은 독립이라 무영향). bandwidth 회복 후 `true` 로 되돌리고 공백을 windowed 백필로 소급한다(소스별 복구성은 statemachine.tf `us_fmp_ingest_jobs` 주석).
