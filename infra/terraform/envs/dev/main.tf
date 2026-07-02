locals {
  prefix = "edge-dev"
}

# ── DNS / TLS (기존 edgesignal.dev 영역은 참조만) ───────
# .dev 는 HSTS preload(강제 HTTPS)라 ACM 인증서가 필수.
data "aws_route53_zone" "main" {
  name = var.route53_zone_name
}

resource "aws_acm_certificate" "edge" {
  domain_name       = var.edge_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ACM DNS 검증 레코드를 Route53 에 자동 생성
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.edge.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.main.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "edge" {
  certificate_arn         = aws_acm_certificate.edge.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# 엣지 도메인 → ALB (ALIAS)
resource "aws_route53_record" "edge" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.edge_domain
  type    = "A"

  alias {
    name                   = module.edge_alb.dns_name
    zone_id                = module.edge_alb.zone_id
    evaluate_target_health = true
  }
}

# ── 네트워크(VPC·서브넷·NAT) ────────────────────────────
module "network" {
  source   = "../../modules/network"
  name     = local.prefix
  vpc_cidr = var.vpc_cidr
  # dev: NAT 1개 공유. prod 에서는 single_nat_gateway=false 로 AZ당 1개.
}

# ── 서비스 클러스터(API 상시 가동) ──────────────────────
# 워커(data-pipeline·analysis-engine) 클러스터는 별도(edge-dev-worker)로 분리 예정.
module "service_cluster" {
  source         = "../../modules/ecs-cluster"
  name           = "${local.prefix}-service"
  namespace_name = "edge.internal"
}

# ── RDS (PostgreSQL, private) ───────────────────────────
module "rds" {
  source     = "../../modules/rds"
  name       = local.prefix
  vpc_id     = module.network.vpc_id
  subnet_ids = module.network.private_subnet_ids
}

# widget-api SG → RDS 5432. 여기(env)에서 독립 리소스로 건다.
# rds 모듈 안에서 widget_api SG 를 참조하면 widget_api↔rds 순환 의존이 되므로.
resource "aws_vpc_security_group_ingress_rule" "rds_from_widget_api" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.widget_api.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "widget-api svc to postgres"
}

# ── 공개 엣지 ALB (임시: widget-api 검증용) ─────────────
# 목표 토폴로지에서는 gateway 앞에 선다. gateway 증분에서 타깃그룹을
# gateway 서비스로 갈아끼우고, widget-api 는 다시 private 으로 닫는다.
module "edge_alb" {
  source = "../../modules/alb"

  name              = "${local.prefix}-edge"
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  target_port       = 8080
  health_check_path = "/actuator/health"
  allowed_cidrs     = var.alb_allowed_cidrs
  enable_https      = true
  certificate_arn   = aws_acm_certificate_validation.edge.certificate_arn
}

# ── widget-api (현재 임시로 ALB 뒤에서 공개 검증) ───────
# gateway 도입 시: target_group_arn 제거 + ingress 를 gateway SG 로 교체.
module "widget_api" {
  source = "../../modules/ecs-service"

  name   = "widget-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.widget_api_image
  container_port   = 8080
  cpu_architecture = "X86_64" # ECR 에 올린 amd64 이미지와 일치

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1

  # ALB 에서만 인바운드 허용 + 타깃그룹 등록
  ingress_security_group_ids        = [module.edge_alb.security_group_id]
  target_group_arn                  = module.edge_alb.target_group_arn
  health_check_grace_period_seconds = 120 # JVM 부팅 ~38s 대비

  # DB 접속 정보 주입. 비밀번호만 Secrets Manager(RDS 관리형)에서, 나머지는 평문 env.
  # (앱쪽 application.yaml 의 DataSource 재활성화는 후속 — 주입돼도 미사용이면 무해)
  environment = {
    SPRING_DATASOURCE_URL      = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
    SPRING_DATASOURCE_USERNAME = module.rds.master_username
  }
  secrets = {
    SPRING_DATASOURCE_PASSWORD = "${module.rds.master_user_secret_arn}:password::"
  }
  secret_arns = [module.rds.master_user_secret_arn]
}

# ── 내부 API (internal-only 스테이징) ────────────────────
# tenant-console-api·super-admin-api 는 아직 호출자(gateway 라우팅)가 없어
# private 서브넷에 Service Connect 로 등록만 하고 대기(idle)한다.
# ALB 타깃·인바운드 허용자 없음 — 호출자가 생기면 ingress_security_group_ids 로 연다.
module "tenant_console_api" {
  source = "../../modules/ecs-service"

  name   = "tenant-console-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.tenant_console_api_image
  container_port   = 8080
  cpu_architecture = "X86_64" # ECR amd64 이미지와 일치

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1
}

# ── 스키마 마이그레이션 one-off task (배포 파이프라인이 트리거) ──────────
# GitHub-hosted 러너는 VPC 밖이라 private RDS 에 못 붙는다. Flyway 는 이 VPC 내부 task 에서
# 실행하고, GitHub Actions 는 OIDC 로 이 task 를 RunTask 트리거만 한다. 접속값은 RDS 관리형
# 시크릿을 재사용(비밀번호만 시크릿, url/user 는 평문) — widget-api 와 동일 패턴.
module "schema_migrate" {
  source = "../../modules/schema-migrate"

  name   = "${local.prefix}-schema-migrate"
  region = var.region
  vpc_id = module.network.vpc_id

  flyway_url  = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
  flyway_user = module.rds.master_username
  # base ARN 을 넘긴다(접미사 없이). 모듈이 valueFrom 에 ':password::' 를 붙이고, IAM 은 base ARN 을 쓴다.
  flyway_password_secret_arn = module.rds.master_user_secret_arn

  cpu_architecture = "X86_64" # 워크플로가 빌드하는 amd64 이미지와 일치
}

# 마이그레이션 task SG → RDS 5432. widget-api 와 동일하게 env 에서 독립 리소스로(순환 의존 회피).
resource "aws_vpc_security_group_ingress_rule" "rds_from_schema_migrate" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.schema_migrate.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "schema-migrate task to postgres"
}

# GitHub Actions(development environment) → AWS OIDC 배포 역할. 마이그레이션 이미지 push + RunTask 최소 권한.
module "gha_deploy_dev" {
  source = "../../modules/github-oidc-deploy"

  name                = "${local.prefix}-gha-schema-migrate"
  github_org_repo     = var.github_org_repo
  github_environments = ["development"] # deploy-dev.yml 의 environment: development 와 일치(OIDC sub)

  create_oidc_provider = var.create_github_oidc_provider
  oidc_provider_arn    = var.github_oidc_provider_arn # create_oidc_provider=false 일 때 기존 provider ARN

  ecr_repository_arn     = module.schema_migrate.ecr_repository_arn
  ecs_cluster_arn        = module.service_cluster.cluster_arn
  task_definition_family = module.schema_migrate.task_definition_family
  pass_role_arns         = [module.schema_migrate.execution_role_arn, module.schema_migrate.task_role_arn]
  log_group_arn          = module.schema_migrate.log_group_arn
}

module "super_admin_api" {
  source = "../../modules/ecs-service"

  name   = "super-admin-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.super_admin_api_image
  container_port   = 8080
  cpu_architecture = "X86_64" # ECR amd64 이미지와 일치

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1
}

# ── news-pipeline 워커 (CDK 스택에서 이관, ALPHA-304) ────
# SFN 8단계 배치가 edge-dev-worker 클러스터에서 Fargate one-off task 로 돈다.
# 구 CDK 스택(news-pipeline-dev-*)과 병행 배포 → 데이터 이관 → 컷오버 순서라,
# 스케줄은 기본 DISABLED 로 만들어지고 컷오버 때 schedule_enabled=true 로 켠다.

data "aws_caller_identity" "current" {}

# raw/curated 버킷 — 버킷명은 글로벌 유니크라 계정 ID 를 접미사로.
# force_destroy 는 dev 전용(스택 철거 시 오브젝트째 삭제 허용).
resource "aws_s3_bucket" "pipeline_raw" {
  bucket        = "${local.prefix}-pipeline-raw-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket" "pipeline_curated" {
  bucket        = "${local.prefix}-pipeline-curated-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

module "news_pipeline" {
  source = "../../modules/sfn-pipeline"

  name   = "${local.prefix}-worker"
  region = var.region

  vpc_id = module.network.vpc_id
  # NAT 비용 회피: 퍼블릭 서브넷 + 퍼블릭 IP 로 아웃바운드(이미지 pull·외부 API).
  subnet_ids       = module.network.public_subnet_ids
  assign_public_ip = true

  container_image  = var.news_pipeline_image
  cpu_architecture = "X86_64" # ECR amd64 이미지와 일치

  # 현행 CDK 태스크 정의와 동일한 앱 설정. DB 는 통합 RDS(market 스키마)로 교체 —
  # 앱은 DB_SECRET_ARN_REF 시크릿을 런타임에 직접 읽는다(주입 아님 → 태스크 역할 권한).
  environment = {
    PROJECT                 = "news-pipeline"
    ENV                     = "dev"
    AWS_REGION_NAME         = var.region
    RAW_BUCKET              = aws_s3_bucket.pipeline_raw.id
    CURATED_BUCKET          = aws_s3_bucket.pipeline_curated.id
    DB_SECRET_ARN_REF       = module.rds.master_user_secret_arn
    DB_SCHEMA               = "market"
    NEWS_SOURCES            = "google_news,fmp"
    NEWS_SOURCE             = "stockinfo7"
    NER_ENABLED             = "false"
    RUN_MODE                = "incremental"
    RUN_ID                  = "manual"
    FMP_PRICE_LOOKBACK_DAYS = "7"
    FMP_FINANCIAL_LIMIT     = "8"
    PREFLIGHT_TERMS_OK      = "true"
    CONTACT_EMAIL           = "asm.alphaeveryday@gmail.com"
  }
  inference_environment = {
    TRANSFORMERS_CACHE = "/tmp/hf"
    HF_HOME            = "/tmp/hf"
    ARTIFACTS_DIR      = "model_artifacts_temporal"
    LLM_BASE_URL       = "https://api.openai.com/v1"
    LLM_MODEL          = "gpt-4o-mini"
  }

  # API 키 시크릿은 수동 생성 자원이라 CDK 스택 삭제와 무관 — 기존 것을 그대로 참조.
  secrets = {
    FMP_API_KEY = var.news_pipeline_fmp_secret_arn
  }
  inference_secrets = {
    OPENAI_API_KEY = var.news_pipeline_openai_secret_arn
    LLM_API_KEY    = var.news_pipeline_openai_secret_arn
  }
  secret_arns = [
    var.news_pipeline_fmp_secret_arn,
    var.news_pipeline_openai_secret_arn,
  ]
  runtime_secret_arns = [module.rds.master_user_secret_arn]

  s3_bucket_arns = [
    aws_s3_bucket.pipeline_raw.arn,
    aws_s3_bucket.pipeline_curated.arn,
  ]

  # 현행 SFN 8단계와 동일한 순서. analyze_daily 만 고사양(추론) 태스크 정의.
  steps = [
    { command = "collect" },
    { command = "alias_map" },
    { command = "persist" },
    { command = "fmp_price_collect" },
    { command = "fmp_financial_collect" },
    { command = "us_news_ingest" },
    { command = "compute_ff5" },
    { command = "analyze_daily", inference = true },
  ]
}

# 워커 task SG → RDS 5432 (persist 등 DB 쓰는 단계). env 레벨 독립 리소스(순환 의존 회피).
resource "aws_vpc_security_group_ingress_rule" "rds_from_news_pipeline" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.news_pipeline.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "news-pipeline worker tasks to postgres"
}
