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

# ── 스키마 마이그레이션 배포용 값 ──────────────────────────
# 아래 값들을 GitHub 저장소의 development environment 변수(vars.*)로 넣으면 deploy-dev 워크플로가 쓴다.
# (secret 이 아니라 식별자라 vars 로 충분. terraform apply 후 `terraform output` 으로 확인.)
output "gha_deploy_role_arn" {
  description = "→ vars.AWS_DEPLOY_ROLE_ARN"
  value       = module.gha_deploy_dev.role_arn
}

output "schema_migrate_ecr_repository_url" {
  description = "→ vars.MIGRATE_ECR_REPOSITORY"
  value       = module.schema_migrate.ecr_repository_url
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

# ── news-pipeline 워커 ─────────────────────────────────
output "news_pipeline_state_machine_arn" {
  description = "수동 실행·컷오버 검증에 사용"
  value       = module.news_pipeline.state_machine_arn
}

output "news_pipeline_raw_bucket" {
  description = "데이터 이관(s3 sync) 대상"
  value       = aws_s3_bucket.pipeline_raw.id
}

output "news_pipeline_curated_bucket" {
  description = "데이터 이관(s3 sync) 대상"
  value       = aws_s3_bucket.pipeline_curated.id
}

output "news_pipeline_alarms_topic_arn" {
  description = "실패 알림 SNS — 이메일 구독은 수동으로 건다"
  value       = module.news_pipeline.sns_topic_arn
}

