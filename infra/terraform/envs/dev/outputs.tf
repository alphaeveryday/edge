output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "service_cluster_name" {
  value = module.service_cluster.cluster_name
}

output "widget_api_service_name" {
  value = module.widget_api.service_name
}

output "widget_api_security_group_id" {
  description = "gateway 도입 시 이 SG 를 ingress 허용 대상으로 참조"
  value       = module.widget_api.security_group_id
}

output "alb_dns_name" {
  description = "ALB 직접 DNS"
  value       = module.edge_alb.dns_name
}

output "rds_endpoint" {
  description = "RDS address:port"
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "RDS 관리형 마스터 비밀번호 시크릿 ARN"
  value       = module.rds.master_user_secret_arn
}

output "edge_url" {
  description = "임시 검증용 공개 URL(HTTPS)"
  value       = "https://${var.edge_domain}"
}

# ── 프론트 CDN ───────────────────────────────────────────
output "widget_url" {
  value = module.widget_site.url
}

output "console_url" {
  value = module.tenant_console_site.url
}

# ── news-pipeline ────────────────────────────────────────
output "pipeline_state_machine_arn" {
  description = "수동 검증 실행: aws stepfunctions start-execution --state-machine-arn <이 값>"
  value       = module.pipeline.state_machine_arn
}

# ── 스키마 마이그레이션 배포용 값 (GitHub development environment vars) ──
output "gha_deploy_role_arn" {
  description = "→ vars.AWS_DEPLOY_ROLE_ARN"
  value       = module.gha_deploy_dev.role_arn
}

output "schema_migrate_ecr_repository_url" {
  description = "→ vars.MIGRATE_ECR_REPOSITORY (foundation 의 edge/schema-migrate)"
  value       = data.aws_ecr_repository.schema_migrate.repository_url
}

output "schema_migrate_task_family" {
  description = "→ vars.MIGRATE_TASK_FAMILY"
  value       = module.schema_migrate.task_definition_family
}

output "schema_migrate_cluster_arn" {
  description = "→ vars.ECS_CLUSTER_ARN"
  value       = module.service_cluster.cluster_arn
}

output "schema_migrate_subnet_ids" {
  description = "→ vars.MIGRATE_SUBNET_IDS (쉼표구분). private 서브넷."
  value       = join(",", module.network.private_subnet_ids)
}

output "schema_migrate_security_group_id" {
  description = "→ vars.MIGRATE_SECURITY_GROUP_ID"
  value       = module.schema_migrate.security_group_id
}

output "schema_migrate_log_group" {
  description = "→ vars.MIGRATE_LOG_GROUP"
  value       = module.schema_migrate.log_group_name
}

output "admin_url" {
  value = module.super_admin_site.url
}
