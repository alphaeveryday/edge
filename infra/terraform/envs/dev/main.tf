locals {
  prefix                           = "edge-dev"
  data_pipeline_ecr_name           = "edge/pipeline"
  data_pipeline_image_tag          = "data-pipeline-latest"
  analysis_engine_image_tag        = "analysis-engine-latest"
  data_pipeline_ecr_repository_arn = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${local.data_pipeline_ecr_name}"
  data_pipeline_ecr_repository_url = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/${local.data_pipeline_ecr_name}"
}

# ── DNS / TLS ───────────────────────────────────────────
# 존은 도메인 등록(수동)으로 생긴 것을 참조. 인증서는 foundation 의 와일드카드를 쓴다
# (ALB=apne2, CloudFront=us-east-1) — env 는 발급하지 않고 data 로 조회만.
data "aws_route53_zone" "main" {
  name = var.route53_zone_name
}

data "aws_acm_certificate" "wildcard_cdn" {
  provider    = aws.us_east_1
  domain      = "*.${var.route53_zone_name}"
  statuses    = ["ISSUED"]
  most_recent = true
}

# ALB(apne2)용 와일드카드 — CloudFront 용(us-east-1)과 리전이 달라 별도 조회.
data "aws_acm_certificate" "wildcard_alb" {
  domain      = "*.${var.route53_zone_name}"
  statuses    = ["ISSUED"]
  most_recent = true
}

# foundation 이 소유한 GitHub OIDC provider·이미지 ECR 을 data 로 참조(느슨한 결합).
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_caller_identity" "current" {}

data "aws_ecr_repository" "schema_migrate" {
  name = "edge/schema-migrate"
}

# 앱 배포(deploy-app.yml)가 이미지를 push 할 저장소 — foundation 소유(edge/<app>).
# CD 배포 역할에 push 권한을 스코프하기 위해 ARN 을 조회한다.
data "aws_ecr_repository" "super_admin_api" {
  name = "edge/super-admin-api"
}

# ⚠️ apply 순서: 이 저장소는 foundation(수동 apply)이 만든다. foundation 적용 전에 dev 가
# apply 되면(머지 시 terraform-apply.yml 자동 실행 포함) 여기서 조회 실패로 중단된다 —
# 의도된 순서 강제 장치(infra/terraform/README "apply 순서"). foundation 적용 후 재실행하면 된다.
data "aws_ecr_repository" "tenant_sync_api" {
  name = "edge/tenant-sync-api"
}

# data-pipeline 의 tag-news·analyze 페이즈가 함께 읽는 DeepSeek API 키 시크릿 — 그릇이 TF 밖
# CLI 로 먼저 생겨 모듈 소유가 아니다(data 로 조회). 이름의 네임스페이스는 data-pipeline 관례.
# 값은 TF 밖 수동 주입: aws secretsmanager put-secret-value --secret-id <name> --secret-string '{"api_key":"..."}'.
data "aws_secretsmanager_secret" "deepseek" {
  name = "${local.prefix}-data-pipeline/deepseek/api-key"
}

# super-admin 부트스트랩 운영자 비밀번호(ALPHA-618) — 그릇이 TF 밖 CLI 로 먼저 생겨 모듈
# 소유가 아니다(data 조회, deepseek 키와 같은 규율 — 값 없는 시크릿을 task 가 참조하면 기동이
# 실패하므로 그릇+값 선생성이 순서 강제 장치다). 값 교체는 TF 밖 수동:
# aws secretsmanager put-secret-value --secret-id <name> --secret-string '{"password":"..."}'.
data "aws_secretsmanager_secret" "admin_bootstrap_operator" {
  name = "${local.prefix}-super-admin-api/bootstrap-operator/password"
}

# ── 네트워크(VPC·3-tier 서브넷·NAT) ─────────────────────
module "network" {
  source             = "../../modules/network"
  name               = local.prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = ["${var.region}a", "${var.region}c"] # a·c 고정(b 회피)
  # dev: NAT 1개 공유. prod 에서는 single_nat_gateway=false 로 AZ당 1개.
}

# ── 클러스터: 상시 API(service) / 배치(worker) 분리 ─────
module "service_cluster" {
  source         = "../../modules/ecs-cluster"
  name           = "${local.prefix}-service"
  namespace_name = "edge.internal"
}

module "worker_cluster" {
  source         = "../../modules/ecs-cluster"
  name           = "${local.prefix}-worker"
  namespace_name = "edge-worker.internal"
}

# ── RDS (PostgreSQL, 격리된 data tier) ──────────────────
module "rds" {
  source     = "../../modules/rds"
  name       = local.prefix
  vpc_id     = module.network.vpc_id
  subnet_ids = module.network.data_subnet_ids # 격리 데이터 tier(컴퓨트와 분리)
}

# ── super-admin 공개 엣지 (ADR-0034, ALPHA-473) ─────────
# 운영 콘솔 API 를 전용 ALB 로 직결한다 — 호스트 단위 1:1, 경로 라우팅 없음. sync ALB 와
# 진입점을 공유하지 않는 이유는 mTLS 가 리스너 단위라서(공유 시 운영자 브라우저까지
# 클라이언트 인증서 강제). WAFv2(AWS Managed rules)는 아래에서 이 ALB 에 부착한다(ALPHA-297).
# 앱 인증은 커스텀 세션 필터(AdminAuthFilter, fail-closed) — 미인증 /api/** 는 401,
# actuator health 만 무인증. 표준 인증·인가 고도화(IdP·CSRF)는 ALPHA-474 — cross-tenant
# 운영자 표면은 운영자 인증·인가를 요구한다(ADR-0008). 이 ALB 는 아래 super_admin_site
# CloudFront 의 /api/* 오리진이기도 하다(ALPHA-615).
# tenant-console-api 는 onprem 플레인 앱(ADR-0029)이라 dev ECS 에서 제거됐다 —
# 실 배포처는 데모 박스 compose 스택(ADR-0033).
module "super_admin_alb" {
  source = "../../modules/alb"

  name              = "${local.prefix}-admin"
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids

  enable_https    = true
  certificate_arn = data.aws_acm_certificate.wildcard_alb.arn
}

module "super_admin_api" {
  source = "../../modules/ecs-service"

  name   = "super-admin-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.super_admin_api_image
  container_port   = 8080
  cpu_architecture = "X86_64"

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1

  target_group_arn           = module.super_admin_alb.target_group_arn
  ingress_security_group_ids = [module.super_admin_alb.security_group_id]

  # 부팅 122s(0.25 vCPU) > 기본 120s — grace 만료 시점에 unhealthy 로 태스크가 킬되는
  # 롤아웃 실패 실증(ALPHA-688, deploy run 30732194158 / failedTasks 5+). 화면이 늘며
  # 기동이 59s→110s 로 올라와 마진이 끊겼다. tenant_sync_api(ALPHA-604)와 같은 처방.
  health_check_grace_period_seconds = 300

  # 앱이 JPA(JDBC)를 갖게 되면서 dev RDS 배선이 필수다(ALPHA-526) — 미주입 시 localhost
  # 폴백으로 부팅 실패(ddl-auto=validate 가 기동 시 접속). 비밀번호는 RDS 관리형 시크릿 주입.
  environment = {
    SPRING_DATASOURCE_URL      = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
    SPRING_DATASOURCE_USERNAME = module.rds.master_username
  }
  secrets = {
    SPRING_DATASOURCE_PASSWORD = "${module.rds.master_user_secret_arn}:password::"
    # 부트스트랩 운영자 활성화(ALPHA-618) — 미주입 시 활성 계정 0(fail-closed 로그인 불가).
    # apply 는 task-def 새 리비전(baseline)만 등록한다 — 실행 서비스 반영은 CD 롤아웃이 한다
    # (CD 가 family 최신 리비전을 복제해 이미지만 교체 → 시크릿 자연 승계). baseline 을
    # update-service 로 직접 굴리지 마라 — 이미지가 tfvars 핀(생성 기준선)이라 실행 중인
    # semver 이미지를 되돌린다(infra/terraform/README "이미지 태그" 참조).
    ADMIN_BOOTSTRAP_OPERATOR_PASSWORD = "${data.aws_secretsmanager_secret.admin_bootstrap_operator.arn}:password::"
  }
  secret_arns = [
    module.rds.master_user_secret_arn,
    data.aws_secretsmanager_secret.admin_bootstrap_operator.arn,
  ]

  # target group ARN 참조만으로는 리스너 생성을 기다리지 않는다(sync 와 동일한 fresh apply 경쟁).
  depends_on = [module.super_admin_alb]
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_super_admin_api" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.super_admin_api.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "super-admin-api to postgres"
}

resource "aws_route53_record" "admin_api" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.admin_api_domain
  type    = "A"

  alias {
    name                   = module.super_admin_alb.dns_name
    zone_id                = module.super_admin_alb.zone_id
    evaluate_target_health = false
  }
}

# ── super-admin ALB WAFv2 (ALPHA-297) ───────────────────
# 공개 엣지의 최소 보안 백스톱 — AWS Managed rules 2종(공통 취약점 + known bad inputs)을
# 기본 차단 동작(override 없음)으로 부착한다. 커스텀 룰·레이트리밋은 범위 밖(후속).
# sync ALB 는 대상이 아니다 — trust store(mTLS)가 게이트(ALPHA-447).
resource "aws_wafv2_web_acl" "super_admin" {
  name  = "${local.prefix}-admin"
  scope = "REGIONAL" # ALB 용 — CloudFront(GLOBAL/us-east-1)가 아니다

  default_action {
    allow {}
  }

  rule {
    name     = "aws-managed-common"
    priority = 1

    # override_action none = 룰 그룹 자체의 차단 동작을 그대로 쓴다(count 로 무력화하지 않음).
    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-managed-common"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "aws-managed-known-bad-inputs"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-managed-known-bad-inputs"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.prefix}-admin"
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "super_admin_alb" {
  resource_arn = module.super_admin_alb.arn
  web_acl_arn  = aws_wafv2_web_acl.super_admin.arn
}

# ── Sync 채널 공개 엣지 (ADR-0034) ──────────────────────
# 진입점은 호스트 단위 1:1 — sync 전용 ALB, 경로 라우팅 없음. 온프렘 sync-agent 가
# sync-dev.edgesignal.dev:443 으로 outbound-Pull 한다(항상 온프렘→클라우드 단방향).
# ⚠️ mTLS 는 2단계: CA·trust store 준비(ALPHA-447) 전까지 mtls_trust_store_arn=null 이라
# 이 엔드포인트는 공개 도달이다(dev 스텁·시드 데이터 전제). trust store 주입이 게이트를 닫는다.
module "sync_alb" {
  source = "../../modules/alb"

  name              = "${local.prefix}-sync"
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids

  enable_https    = true
  certificate_arn = data.aws_acm_certificate.wildcard_alb.arn

  mtls_trust_store_arn = var.sync_mtls_trust_store_arn
}

module "tenant_sync_api" {
  source = "../../modules/ecs-service"

  name   = "tenant-sync-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.tenant_sync_api_image
  container_port   = 8080
  cpu_architecture = "X86_64"

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1

  # 부팅 93s(0.25 vCPU) + ALB healthy 플립 ~60s(연속 200 2회×30s) > 기본 120s —
  # grace 만료 시점에 unhealthy 로 태스크가 킬되는 롤아웃 실패 실증(ALPHA-604,
  # deploy run 30348946092 4연속). 라이브는 CLI 로 선적용됨 — 이 값은 드리프트 해소.
  health_check_grace_period_seconds = 300

  # tenant_delivery(outbox) 조회 — 앱이 JDBC 를 갖게 되면서 dev RDS 배선이 필수다
  # (미주입 시 localhost 폴백 → DB health DOWN). 비밀번호는 RDS 관리형 시크릿 주입.
  environment = {
    SPRING_DATASOURCE_URL      = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
    SPRING_DATASOURCE_USERNAME = module.rds.master_username
  }
  secrets = {
    SPRING_DATASOURCE_PASSWORD = "${module.rds.master_user_secret_arn}:password::"
  }
  secret_arns = [module.rds.master_user_secret_arn]

  # 인바운드는 sync ALB 에서만 — 태스크 직접 도달을 막아야 mTLS 헤더
  # (X-Amzn-Mtls-Clientcert-*) 신뢰가 성립한다(ALB 우회 경로 없음).
  target_group_arn           = module.sync_alb.target_group_arn
  ingress_security_group_ids = [module.sync_alb.security_group_id]

  # target group ARN 참조만으로는 리스너 생성을 기다리지 않는다 — LB 미연결 target group 으로
  # 서비스를 만들면 ECS 가 거부하므로(fresh apply 경쟁) 모듈 전체를 명시 의존한다.
  depends_on = [module.sync_alb]
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_tenant_sync_api" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.tenant_sync_api.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "tenant-sync-api to postgres"
}

resource "aws_route53_record" "sync" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.sync_domain
  type    = "A"

  alias {
    name                   = module.sync_alb.dns_name
    zone_id                = module.sync_alb.zone_id
    evaluate_target_health = false
  }
}

# ── 스키마 마이그레이션 one-off task ────────────────────
# ECR 은 foundation(edge/schema-migrate) 소유 — data 로 조회해 넘긴다(decoupled).
module "schema_migrate" {
  source = "../../modules/schema-migrate"

  name   = "${local.prefix}-schema-migrate"
  region = var.region
  vpc_id = module.network.vpc_id

  ecr_repository_url = data.aws_ecr_repository.schema_migrate.repository_url
  ecr_repository_arn = data.aws_ecr_repository.schema_migrate.arn

  flyway_url                 = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
  flyway_user                = module.rds.master_username
  flyway_password_secret_arn = module.rds.master_user_secret_arn

  cpu_architecture = "X86_64"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_schema_migrate" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.schema_migrate.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "schema-migrate task to postgres"
}

# ── 에이전트 읽기전용 질의 one-off task (ALPHA-622) ──────
# private RDS 는 VPC 밖에서 못 붙는다 — schema-migrate 와 같은 해법(VPC 내부 one-off task).
# 이미지는 analysis 페이즈와 동일한 것을 쓴다(질의 코드가 같은 파이썬 패키지에 산다) —
# 아래 data_pipeline 의 analysis_image 와 표현식을 공유해 태그가 갈라지지 않게 한다.
module "db_query" {
  source = "../../modules/db-query"

  name   = "${local.prefix}-db-query"
  region = var.region
  vpc_id = module.network.vpc_id

  # IAM DB 인증 토큰 정책(rds-db:connect)이 계정·DB 리소스 id 로 스코프된다 — 비밀번호가 없는 이유.
  account_id     = data.aws_caller_identity.current.account_id
  db_resource_id = module.rds.resource_id

  image = "${local.data_pipeline_ecr_repository_url}:${local.analysis_engine_image_tag}"

  db_host = module.rds.address
  db_port = module.rds.port
  db_name = module.rds.db_name

  cpu_architecture = "X86_64"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_db_query" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.db_query.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "db-query task to postgres"
}

# GitHub Actions → AWS OIDC 배포 역할. provider 는 foundation 소유(create=false + data ARN).
module "gha_deploy_dev" {
  source = "../../modules/github-oidc-deploy"

  name            = "${local.prefix}-gha-schema-migrate"
  github_org_repo = var.github_org_repo
  # 신뢰 경계 = dev 브랜치 ref 만(ALPHA-313). 배포 워크플로는 `environment:` 없이 push:dev 로 돌아
  # sub=ref:refs/heads/dev 가 된다. environment 기반 신뢰는 두지 않는다(Free 플랜은 브랜치 핀 불가).
  github_branch_refs = ["refs/heads/dev"]

  create_oidc_provider = false
  oidc_provider_arn    = data.aws_iam_openid_connect_provider.github.arn

  ecr_repository_arn     = data.aws_ecr_repository.schema_migrate.arn
  ecs_cluster_arn        = module.service_cluster.cluster_arn
  task_definition_family = module.schema_migrate.task_definition_family
  pass_role_arns         = [module.schema_migrate.execution_role_arn, module.schema_migrate.task_role_arn]
  log_group_arn          = module.schema_migrate.log_group_arn

  # 앱/배치 이미지 push 권한 — 백엔드 앱(super-admin-api·tenant-sync-api) + data-pipeline 배치 이미지.
  app_ecr_repository_arns = [
    data.aws_ecr_repository.super_admin_api.arn,
    data.aws_ecr_repository.tenant_sync_api.arn,
    local.data_pipeline_ecr_repository_arn,
  ]
  app_service_arns = concat([
    module.super_admin_api.service_arn,
    module.tenant_sync_api.service_arn,
    # 1분 상주 서비스(ALPHA-711) — deploy-data-pipeline.yml 이 이미지 push 뒤
    # force-new-deployment 로 mutable 태그를 다시 당기게 한다(없으면 상주 프로세스가
    # 배포 후에도 옛 이미지를 계속 돈다).
  ], module.data_pipeline.minute_service_arns)
  app_pass_role_arns = [
    module.super_admin_api.execution_role_arn, module.super_admin_api.task_role_arn,
    module.tenant_sync_api.execution_role_arn, module.tenant_sync_api.task_role_arn,
  ]

  # UI 배포(deploy-ui.yml) 권한 — 프론트 S3 sync + CloudFront 무효화.
  # tenant-console 은 온프렘 플레인이라 cloud 배포처 없음(ADR-0032) — super-admin 만.
  ui_bucket_arns = [
    module.super_admin_site.bucket_arn,
  ]
  ui_distribution_arns = [
    module.super_admin_site.distribution_arn,
  ]
}

# ── pipeline lake (구 news-pipeline SFN 의 존치 자원) ────
# 레거시 SFN(edge-dev-pipeline)은 ALPHA-549 에서 걷어냈다 — 이 모듈은 이제 아래
# data_pipeline 이 소비하는 lake S3 버킷만 소유한다.
module "pipeline" {
  source = "../../modules/pipeline"

  name = "${local.prefix}-pipeline"
}

# ── data-pipeline (Step Functions 배치: raw → normalize → feature → analyze) ──
# 기존 임시 news-pipeline SFN 과 분리된 상태머신. 최초엔 DISABLED 로 생성한다.
# analyze 페이즈는 구 analysis-engine 모듈의 흡수다(ALPHA-408) — 이미지는 alphamale 코드베이스라 따로다.
module "data_pipeline" {
  source = "../../modules/data-pipeline"

  name             = "${local.prefix}-data-pipeline"
  region           = var.region
  vpc_id           = module.network.vpc_id
  subnet_ids       = module.network.private_subnet_ids
  cluster_arn      = module.worker_cluster.cluster_arn
  image            = "${local.data_pipeline_ecr_repository_url}:${local.data_pipeline_image_tag}"
  analysis_image   = "${local.data_pipeline_ecr_repository_url}:${local.analysis_engine_image_tag}"
  lake_bucket_name = module.pipeline.lake_bucket
  lake_bucket_arn  = module.pipeline.lake_bucket_arn

  # feature 페이즈(ALPHA-386): tag-news 는 DeepSeek 키, load-* 는 RDS 접속이 필요하다.
  # deepseek 시크릿은 tag-news 와 analyze 페이즈가 함께 읽는다(그릇은 기존 CLI 생성분 — data 조회).
  db_host                = module.rds.address
  db_port                = module.rds.port
  db_name                = module.rds.db_name
  db_user                = module.rds.master_username
  db_password_secret_arn = module.rds.master_user_secret_arn
  deepseek_secret_arn    = data.aws_secretsmanager_secret.deepseek.arn

  # ExposureReverted 회수 집행(ALPHA-746) — analysis-consumer 가 super-admin 무효화 API 를
  # 부른다. 내부 경로(Service Connect)가 아닌 공개 엣지인 이유: 소비자는 worker_cluster,
  # super-admin-api 는 service_cluster 네임스페이스라 디스커버리가 닿지 않는다 — NAT egress
  # 로 ALB(admin_api_domain, WAF 부착)를 탄다. 자격은 SSM SecureString 수동 주입
  # (modules/data-pipeline/minute_services.tf 의 파라미터 이름 계약 참조).
  super_admin_api_url = "https://${var.admin_api_domain}"

  # explanation_run 번들 고정 — dev RDS 의 release_bundle(PUBLISHED) 시딩 행과 일치해야
  # explanation_result 가 RDS 로 영속된다. 미주입은 이제 선택지가 아니다(ALPHA-797 이
  # S3 폴백을 폐기) — 변수에 기본값이 없어 plan 이 막는다. 잠정 번들(ALPHA-406) —
  # 정식 버저닝은 릴리스 규약 합의 후.
  analysis_release_bundle_version = "dev-mvp-0"

  # 시각창 집계 Athena 오프로드(ALPHA-780). 5분봉 Iceberg 정본과 Athena 결과 CSV 가 같은
  # 버킷에 산다 — **terraform 관리 밖**이라 ARN 만 넘기고 리소스로 잡지 않는다.
  # 이것 없이는 구간 모드가 DuckDB 폴백(질의당 376MB)으로 떨어져 1분 주기를 못 버틴다.
  analysis_market_data_bucket_arn = "arn:aws:s3:::market-data-${data.aws_caller_identity.current.account_id}"
  analysis_athena_workgroup       = "market_data"

  # 컷오버: raw 전량성공 게이트 제거(ADR-0030) + 일주일치 백필 실증(#178) 후 일일 트리거 활성화.
  schedule_state = "ENABLED"


  # 컷오버(ALPHA-553 PR2): 뉴스 레인 스케줄(15:00·15:30·23:50 KST 평일). 시장 SFN 의 뉴스 스텝
  # 제거와 **같은 apply** 로 켠다 — 같은 event 를 두 SFN 이 동시에 쓰는 겹침 창이 구조적으로
  # 생기지 않는다(PR1 이 DISABLED 로 세워 둔 컷오버 게이트). 수동 e2e 실증(07-27,
  # manual-553-verify-20260727T132646Z)을 선행했다.
  news_schedule_state = "ENABLED"

  # 컷오버(ALPHA-724): 공시 레인 스케줄(평일 09~18시 정각 10슬롯). 시장 SFN 에서 공시 4스텝을
  # 빼는 것과 **같은 apply** 로 켠다 — 한쪽만 먼저 가면 공시가 아무 데서도 안 돌거나(스텝만 제거)
  # 같은 스텝을 두 SFN 이 동시에 소유한다(스케줄만 켬). 후자는 원장 정체성이 깨지는 쪽이다:
  # `catalog.by_cli` 가 CLI 로 작업을 해소하는데 두 레인의 CLI 가 같아, 장중 런의 attempt 가
  # 시장 레인 task_key 로 기록돼 장중은 영구 MISSED·시장은 LEDGER_GAP 이 된다.
  # 병행 신설(ALPHA-722)은 DISABLED 로 세워 plan 으로 검증했다(뉴스 레인 553 PR1→PR2 와 같은 순서).
  # ⚠️ 이 스케줄이 켜지면 `OPS_DISCLOSURE_SCHED_HHMM` 도 함께 주입된다(ops_ledger.tf 조건부) —
  # 그때부터 Reconciler 가 공시 슬롯 결측을 판정한다.
  disclosure_schedule_state = "ENABLED"

  # 장중 수급 레인(ALPHA-769): 평일 5슬롯(09:35·10:05·11:25·13:25·14:35 KST). 모듈 기본이
  # ENABLED 라 이 줄은 **명시일 뿐 값을 바꾸지 않는다** — 그래도 적는 이유는 위 두 레인과 나란히
  # 놓여야 "dev 에서 어떤 레인이 도는가"를 이 파일 하나로 읽을 수 있어서다.
  # 공시·뉴스처럼 DISABLED 신설 → 별도 apply 컷오버를 밟지 않는 이유: 이 3스텝은 시장 SFN 이
  # 돌던 것을 뺏어오는 게 아니라 **배선이 0이던 신설**이라(ALPHA-767·768) 두 레인이 같은 스텝을
  # 동시에 소유하는 겹침 창이 없다.
  # ⚠️ 이 스케줄이 켜져 있으므로 `OPS_INVESTOR_INTRADAY_SCHED_HHMM` 도 함께 주입된다
  # (ops_ledger.tf 조건부) — Reconciler 가 이 5슬롯의 결측을 판정한다.
  investor_intraday_schedule_state = "ENABLED"

  # 컷오버(ALPHA-588): 원장 도입(ALPHA-530) 때 "Planner 첫 스케줄런 검증 후"를 조건으로 미뤄 둔
  # 대조 스케줄. 켜기 전 실제 스케줄 런(`etf-daily:2026-07-27T15:40`, FAILED)에 OPS_RUN_KEY 를
  # 지정해 수동 1회 대조로 판정을 확인했다 — orchestration NULL→FAILED 동기화, 자기 기록이
  # 불가능한 TAG_NEWS(deepseek task-def 에 DB env 없음, ALPHA-181 명시적 제외)가 backfill 로
  # PENDING→FULFILLED, 거짓 이슈 0건.
  # ⚠️ 남는 사각(ALPHA-565): 주기 실행은 `_due_slot()` 의 스케줄 슬롯 하나만 대조한다 — 수동
  # 슬롯은 OPS_RUN_KEY 를 명시해야 대조된다. 켜는 것이 그 사각을 만드는 건 아니다(지금은 아무
  # 런도 대조되지 않으므로 순증).
  reconcile_schedule_state = "ENABLED"

  # 컷오버(ALPHA-712): 1분 상주 서비스 3종의 세션 결속 스케일 업/다운. 08-03 장중에 같은
  # 순서를 손으로 돌려(plan → desired 1 --force-new-deployment → 백로그 소진 → 실시간 도달)
  # 레인 자체는 실증했다 — 이 스케줄은 그 수동 절차를 자동화한 것이다.
  # ⚠️ 첫 발화는 다음 거래일 07:45 KST 다. 그때까지 이미지 CD 가 끝나 있어야 `start-minute-session`
  # 스텝이 존재한다(이미지 CD 와 apply 는 순서 보장이 없다 — deploy-order-splits-the-pr).
  minute_session_schedule_state = "ENABLED"

  # KR 평일 휴장일 2026 (ALPHA-387). 비면 Planner 는 휴장일에도 런을 계획하고, KRX 수집은 그날
  # 오는 직전 거래일 PDF 를 휴장일 as-of 로 오라벨한다 — 주말만 코드가 알기 때문이다.
  # ⚠️ **해마다 갱신해야 한다**(거래소 캘린더 연동 전까지의 수동 주입 지점, trading_calendar.py).
  # 출처: KRX 2026 휴장일 공지 기준. 12-31 은 매년 연말 폐장(마지막 거래일 12-30) 관례다.
  kr_holidays = [
    "2026-01-01", # 신정
    "2026-02-16", # 설 연휴
    "2026-02-17", # 설날
    "2026-02-18", # 설 연휴
    "2026-03-02", # 삼일절 대체(3-1 일요일)
    "2026-05-01", # 근로자의날
    "2026-05-05", # 어린이날
    "2026-05-25", # 부처님오신날
    "2026-06-03", # 제9회 전국동시지방선거(선거일은 법정공휴일 — 거래소도 휴장)
    "2026-08-17", # 광복절 대체(8-15 토요일)
    "2026-09-24", # 추석 연휴
    "2026-09-25", # 추석
    "2026-10-05", # 개천절 대체(10-3 토요일)
    "2026-10-09", # 한글날
    "2026-12-25", # 성탄절
    "2026-12-31", # 연말 폐장
  ]

  alarm_email = var.pipeline_alarm_email
}

# KRX 시크릿 그릇은 ALPHA-336 때 CLI 로 만들어져 TF state 밖에 있었다 — 형제 시크릿(fmp·kis·
# dart)처럼 모듈이 소유하도록 입양한다. 값(mbr_id·pw)은 건드리지 않는다: TF 는 그릇만 만들고
# 버전은 수동 주입하는 게 이 모듈의 관례다(modules/data-pipeline/storage.tf).
# id 가 이름이 아니라 ARN 인 건 프로바이더 규약이고, ARN 의 랜덤 접미사는 환경 고유값이라
# 이 블록이 모듈이 아니라 env 에 있다. 입양 후에는 no-op 이라 남겨 둬도 무해하다.
import {
  to = module.data_pipeline.aws_secretsmanager_secret.krx
  id = "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:edge-dev-data-pipeline/krx/login-LAY4MI"
}

# data-pipeline SG → RDS 5432 (env 에서 독립 리소스로 — 순환 의존 회피)
# load-instruments 가 ECS 안에서 Cloud Event Store 에 쓰려면 필요하다. ALPHA-372 는 이 규칙
# 없이 완료됐는데, 그때 로더를 SSM 터널로 **로컬에서** 돌렸기 때문에 드러나지 않았다
# (티켓에 적힌 "이미 허용됨"은 news-pipeline 모듈의 다른 SG 를 본 오독이다).
resource "aws_vpc_security_group_ingress_rule" "rds_from_data_pipeline" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.data_pipeline.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "data-pipeline batch tasks to postgres"
}

# analysis-engine 모듈은 ALPHA-408 에서 data-pipeline 의 analyze 페이즈로 흡수돼 삭제됐다.
# analyze 태스크의 RDS 접근은 위 rds_from_data_pipeline 규칙이 덮는다(같은 task SG).

# ── 프론트 정적 호스팅 (S3 + CloudFront) ────────────────
# 모듈·S3 이름은 앱 폴더명과 일치(widget-ui·tenant-console-ui·super-admin-ui).
# 인증서는 foundation us-east-1 와일드카드(모든 서브도메인 커버).
# tenant-console 은 온프렘 플레인이라(ADR-0010·0016·0029·0032) cloud 정적 호스팅을 두지 않는다 —
# 콘솔 SPA 는 온프렘 박스가 자기 API 와 같은 오리진(`/`·`/api/v1`)으로 서빙한다(후속 증분, ALPHA-423).
# super-admin 만 cloud 플레인(Vendor Cloud 운영 콘솔).
module "super_admin_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-super-admin" # super-admin-ui (운영 콘솔 SPA)
  domain_name     = var.admin_domain
  zone_id         = data.aws_route53_zone.main.zone_id
  certificate_arn = data.aws_acm_certificate.wildcard_cdn.arn
  spa             = true

  # /api/* → super-admin ALB (ALPHA-615). ALB dns_name 이 아닌 admin_api_domain 을 쓰는
  # 이유: ALB HTTP:80 은 443 으로 301 리다이렉트라 https-only(:443)가 필수인데, CloudFront
  # 의 오리진 인증서 검증(SNI)이 *.edgesignal.dev ACM 과 일치해야 해서다. 콘솔 SPA 와
  # same-origin 이 되어 세션 쿠키(JSESSIONID, Secure·SameSite=Strict)가 실린다.
  # 쓰기 API(POST login·PATCH 정정 등)라 메서드는 7종 전체.
  # SPA fallback 은 CF Function(default behavior 한정)이라 /api/* 의 403/404 JSON 도
  # 왜곡 없이 통과한다(ALPHA-617 — 구 custom_error_response 마스킹 해소).
  api_origin_domain   = var.admin_api_domain
  api_origin_protocol = "https-only"
  api_allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
}

# 모듈 rename 은 state 상 이동으로 처리 — CloudFront 배포를 재생성하지 않고 보존한다.
moved {
  from = module.admin_site
  to   = module.super_admin_site
}

# news_pipeline → pipeline 인스턴스 rename (state 이동으로 보존, 재생성 없음).
moved {
  from = module.news_pipeline
  to   = module.pipeline
}
