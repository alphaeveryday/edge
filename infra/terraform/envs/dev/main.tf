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

# ── 워커 클러스터 (배치: data-pipeline·analysis-engine) ──
module "worker_cluster" {
  source         = "../../modules/ecs-cluster"
  name           = "${local.prefix}-worker"
  namespace_name = "edge-worker.internal"
}

# ── 데이터 레이크 (수집→분석 공유 저장소, edge-data-lake-*) ──
module "data_lake" {
  source      = "../../modules/s3-lake"
  bucket_name = var.lake_bucket_name
}

# FMP API 키 시크릿 껍데기. 값은 apply 후 수동 주입한다(하드코딩 금지):
#   aws secretsmanager put-secret-value --secret-id edge-dev/data-pipeline/fmp \
#     --secret-string '{"api_key":"..."}'
resource "aws_secretsmanager_secret" "fmp" {
  name        = "${local.prefix}/data-pipeline/fmp"
  description = "FMP API key for data-pipeline news collection"
}

# ── 뉴스 수집 배치 (EventBridge Scheduler → Fargate) ────
# 스케줄 주기는 미확정 placeholder — 이미지 push·키 주입 전까지 비활성(enabled=false).
module "data_pipeline" {
  source = "../../modules/ecs-scheduled-task"

  # 앱 리소스는 bare 앱명(widget-api 등 ecs-service 와 동일 규칙),
  # 이미지 저장소는 edge/<앱> 네임스페이스(기존 앱 ECR 과 동일).
  name                = "data-pipeline"
  ecr_repository_name = "edge/data-pipeline"
  region              = var.region
  vpc_id              = module.network.vpc_id
  cluster_arn         = module.worker_cluster.cluster_arn
  subnet_ids          = module.network.private_subnet_ids

  schedules = {
    ingest-raw = {
      schedule_expression = "cron(0 * * * ? *)"
      command             = ["python", "-m", "data_pipeline.run", "ingest-raw"]
      enabled             = false
    }
    normalize = {
      schedule_expression = "cron(20 * * * ? *)"
      command             = ["python", "-m", "data_pipeline.run", "normalize"]
      enabled             = false
    }
  }

  # 앱 설정 오버라이드(config loader 의 DATA_PIPELINE_ 접두 규약).
  environment = {
    DATA_PIPELINE_STORAGE__BACKEND = "s3"
    DATA_PIPELINE_STORAGE__BUCKET  = module.data_lake.bucket_name
  }
  secrets = {
    DATA_PIPELINE_NEWS__SOURCES__FMP__API_KEY = "${aws_secretsmanager_secret.fmp.arn}:api_key::"
  }
  secret_arns = [aws_secretsmanager_secret.fmp.arn]

  # 레이크 프리픽스 R/W 최소 권한 — 파이프라인이 쓰는 프리픽스만.
  task_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [module.data_lake.bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["raw/*", "canonical/*", "operations_archive/*"]
          }
        }
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${module.data_lake.bucket_arn}/raw/*",
          "${module.data_lake.bucket_arn}/canonical/*",
          "${module.data_lake.bucket_arn}/operations_archive/*",
        ]
      },
      # 버킷이 SSE-S3(AES256) 라 KMS 권한 불필요. CMK 로 승격 시 kms 문을 다시 추가한다.
    ]
  })
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
