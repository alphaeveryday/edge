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
