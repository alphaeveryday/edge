locals {
  prefix                           = "edge-dev"
  data_pipeline_ecr_name           = "edge/pipeline"
  data_pipeline_image_tag          = "data-pipeline-latest"
  data_pipeline_ecr_repository_arn = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${local.data_pipeline_ecr_name}"
  data_pipeline_ecr_repository_url = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/${local.data_pipeline_ecr_name}"
}

# ── DNS / TLS ───────────────────────────────────────────
# 존은 도메인 등록(수동)으로 생긴 것을 참조. 인증서는 foundation 의 와일드카드를 쓴다
# (ALB=apne2, CloudFront=us-east-1) — env 는 발급하지 않고 data 로 조회만.
data "aws_route53_zone" "main" {
  name = var.route53_zone_name
}

data "aws_acm_certificate" "wildcard" {
  domain      = "*.${var.route53_zone_name}"
  statuses    = ["ISSUED"]
  most_recent = true
}

data "aws_acm_certificate" "wildcard_cdn" {
  provider    = aws.us_east_1
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
data "aws_ecr_repository" "widget_api" {
  name = "edge/widget-api"
}

data "aws_ecr_repository" "tenant_console_api" {
  name = "edge/tenant-console-api"
}

data "aws_ecr_repository" "super_admin_api" {
  name = "edge/super-admin-api"
}

data "aws_ecr_repository" "gateway" {
  name = "edge/gateway"
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

# widget-api SG → RDS 5432 (env 에서 독립 리소스로 — 순환 의존 회피)
resource "aws_vpc_security_group_ingress_rule" "rds_from_widget_api" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.widget_api.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "widget-api svc to postgres"
}

# ── 공개 엣지 ALB (임시: widget-api 검증용) ─────────────
module "edge_alb" {
  source = "../../modules/alb"

  name              = "${local.prefix}-edge"
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  target_port       = 8080
  health_check_path = "/actuator/health"
  allowed_cidrs     = var.alb_allowed_cidrs
  enable_https      = true
  certificate_arn   = data.aws_acm_certificate.wildcard.arn # foundation apne2 와일드카드
}

# ── widget-api (ALB 뒤 공개 검증) ───────────────────────
module "widget_api" {
  source = "../../modules/ecs-service"

  name   = "widget-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.widget_api_image
  container_port   = 8080
  cpu_architecture = "X86_64"

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1

  ingress_security_group_ids        = [module.edge_alb.security_group_id]
  target_group_arn                  = module.edge_alb.target_group_arn
  health_check_grace_period_seconds = 120

  environment = {
    SPRING_DATASOURCE_URL      = "jdbc:postgresql://${module.rds.endpoint}/${module.rds.db_name}"
    SPRING_DATASOURCE_USERNAME = module.rds.master_username
  }
  secrets = {
    SPRING_DATASOURCE_PASSWORD = "${module.rds.master_user_secret_arn}:password::"
  }
  secret_arns = [module.rds.master_user_secret_arn]
}

# ── 내부 API (호출자 생길 때까지 idle) ──────────────────
module "tenant_console_api" {
  source = "../../modules/ecs-service"

  name   = "tenant-console-api"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.tenant_console_api_image
  container_port   = 8080
  cpu_architecture = "X86_64"

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1
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
}

# ── gateway (엣지 리버스 프록시 — ALPHA-296 internal-only 스테이징) ──
# 공개 ALB 컷오버(ALPHA-294) 전까지 internal-only 로 세워 ecs-service 재사용·Service Connect 등록을 검증한다.
# 라우트 uri 는 Service Connect DNS(widget-api·tenant-console-api·super-admin-api)로 요청 시 lazy 해석 —
# 업스트림 연결 없이 부팅되므로 target_group·ingress 없이 RUNNING 안정화된다(ALB 타깃·컷오버는 ALPHA-294).
module "gateway" {
  source = "../../modules/ecs-service"

  name   = "gateway"
  region = var.region

  cluster_arn                   = module.service_cluster.cluster_arn
  service_connect_namespace_arn = module.service_cluster.namespace_arn

  container_image  = var.gateway_image
  container_port   = 8080
  cpu_architecture = "X86_64"

  vpc_id        = module.network.vpc_id
  subnet_ids    = module.network.private_subnet_ids
  desired_count = 1
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

  # 앱/배치 이미지 push 권한 — 백엔드 앱 4종 + data-pipeline 배치 이미지.
  app_ecr_repository_arns = [
    data.aws_ecr_repository.widget_api.arn,
    data.aws_ecr_repository.tenant_console_api.arn,
    data.aws_ecr_repository.super_admin_api.arn,
    data.aws_ecr_repository.gateway.arn,
    local.data_pipeline_ecr_repository_arn,
  ]
  app_service_arns = [
    module.widget_api.service_arn,
    module.tenant_console_api.service_arn,
    module.super_admin_api.service_arn,
    module.gateway.service_arn,
  ]
  app_pass_role_arns = [
    module.widget_api.execution_role_arn, module.widget_api.task_role_arn,
    module.tenant_console_api.execution_role_arn, module.tenant_console_api.task_role_arn,
    module.super_admin_api.execution_role_arn, module.super_admin_api.task_role_arn,
    module.gateway.execution_role_arn, module.gateway.task_role_arn,
  ]

  # UI 배포(deploy-ui.yml) 권한 — 3개 프론트 S3 sync + CloudFront 무효화.
  ui_bucket_arns = [
    module.widget_site.bucket_arn,
    module.tenant_console_site.bucket_arn,
    module.super_admin_site.bucket_arn,
  ]
  ui_distribution_arns = [
    module.widget_site.distribution_arn,
    module.tenant_console_site.distribution_arn,
    module.super_admin_site.distribution_arn,
  ]
}

# ── news-pipeline (Step Functions 배치) ─────────────────
# CDK 대체 SFN. edge VPC·RDS 통합. 스케줄러는 DISABLED 로 생성(수동 검증 후 컷오버).
module "pipeline" {
  source = "../../modules/pipeline"

  name        = "${local.prefix}-pipeline"
  region      = var.region
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  cluster_arn = module.worker_cluster.cluster_arn
  image       = var.pipeline_image

  db_host                = module.rds.address
  db_port                = module.rds.port
  db_name                = module.rds.db_name
  db_user                = module.rds.master_username
  db_password_secret_arn = module.rds.master_user_secret_arn

  contact_email = var.pipeline_contact_email
  alarm_email   = var.pipeline_alarm_email
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_pipeline" {
  security_group_id            = module.rds.security_group_id
  referenced_security_group_id = module.pipeline.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "news-pipeline batch tasks to postgres"
}

# ── data-pipeline raw ingest (Step Functions 배치) ───────
# 기존 임시 news-pipeline SFN 과 분리된 raw 수집 전용 상태머신. 최초엔 DISABLED 로 생성한다.
module "data_pipeline" {
  source = "../../modules/data-pipeline"

  name             = "${local.prefix}-data-pipeline"
  region           = var.region
  vpc_id           = module.network.vpc_id
  subnet_ids       = module.network.private_subnet_ids
  cluster_arn      = module.worker_cluster.cluster_arn
  image            = "${local.data_pipeline_ecr_repository_url}:${local.data_pipeline_image_tag}"
  lake_bucket_name = module.pipeline.raw_bucket

  alarm_email = var.pipeline_alarm_email
}

# ── 프론트 정적 호스팅 (S3 + CloudFront) ────────────────
# 모듈·S3 이름은 앱 폴더명과 일치(widget-ui·tenant-console-ui·super-admin-ui).
# 인증서는 foundation us-east-1 와일드카드(모든 서브도메인 커버).
module "widget_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-widget" # widget-ui (임베드 위젯, 정적 파일)
  domain_name     = var.widget_domain
  zone_id         = data.aws_route53_zone.main.zone_id
  certificate_arn = data.aws_acm_certificate.wildcard_cdn.arn
  spa             = false
}

module "tenant_console_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-tenant-console" # tenant-console-ui (테넌트 콘솔 SPA)
  domain_name     = var.console_domain
  zone_id         = data.aws_route53_zone.main.zone_id
  certificate_arn = data.aws_acm_certificate.wildcard_cdn.arn
  spa             = true
}

# super-admin-ui: 아직 빈 폴더지만 CDN 자리를 미리 세워둔다(빌드되면 s3 sync 만).
module "super_admin_site" {
  source = "../../modules/static-site"

  name            = "${local.prefix}-super-admin" # super-admin-ui (운영 콘솔 SPA)
  domain_name     = var.admin_domain
  zone_id         = data.aws_route53_zone.main.zone_id
  certificate_arn = data.aws_acm_certificate.wildcard_cdn.arn
  spa             = true
}

# 모듈 rename 은 state 상 이동으로 처리 — CloudFront 배포를 재생성하지 않고 보존한다.
moved {
  from = module.console_site
  to   = module.tenant_console_site
}

moved {
  from = module.admin_site
  to   = module.super_admin_site
}

# news_pipeline → pipeline 인스턴스 rename (state 이동으로 보존, 재생성 없음).
moved {
  from = module.news_pipeline
  to   = module.pipeline
}
