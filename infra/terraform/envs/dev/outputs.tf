output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "service_cluster_name" {
  value = module.service_cluster.cluster_name
}

output "rds_endpoint" {
  description = "RDS address:port"
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "RDS 관리형 마스터 비밀번호 시크릿 ARN"
  value       = module.rds.master_user_secret_arn
}

# ── 프론트 CDN ───────────────────────────────────────────
output "console_url" {
  value = module.tenant_console_site.url
}

# ── news-pipeline ────────────────────────────────────────
output "pipeline_state_machine_arn" {
  description = "수동 검증 실행: aws stepfunctions start-execution --state-machine-arn <이 값>"
  value       = module.pipeline.state_machine_arn
}

# ── data-pipeline (raw → normalize → feature → analyze) ─
output "data_pipeline_state_machine_arn" {
  description = "수동 검증 실행: aws stepfunctions start-execution --state-machine-arn <이 값> --input '{\"run_id\":\"manual-...\"}'"
  value       = module.data_pipeline.state_machine_arn
}

output "data_pipeline_ecr_repository_url" {
  description = "data-pipeline image repository URL (기존 edge/pipeline; analyze 는 같은 저장소의 analysis-engine-latest 태그)"
  value       = local.data_pipeline_ecr_repository_url
}

output "data_pipeline_task_families" {
  description = "data-pipeline ECS task definition families by vendor"
  value       = module.data_pipeline.task_definition_families
}

output "data_pipeline_analysis_task_family" {
  description = "analyze 페이즈 task family — 특정일 수동 재실행: aws ecs run-task 로 이 task-def 를 띄워 Command 를 ['--trade-date','YYYY-MM-DD','--request-id','manual-...'] 로 덮는다"
  value       = module.data_pipeline.analysis_task_definition_family
}

output "data_pipeline_log_group" {
  description = "data-pipeline CloudWatch log group (analyze 포함 — 스트림 접두사 analysis)"
  value       = module.data_pipeline.log_group_name
}

output "data_pipeline_lake_bucket" {
  description = "data-pipeline active storage bucket."
  value       = module.data_pipeline.lake_bucket_name
}

output "pipeline_lake_bucket" {
  description = "pipeline active lake bucket."
  value       = module.pipeline.lake_bucket
}

output "data_pipeline_fmp_secret_arn" {
  description = "FMP API key secret ARN (수동 주입: {\"apikey\":\"...\"})"
  value       = module.data_pipeline.fmp_secret_arn
}

output "data_pipeline_kis_secret_arn" {
  description = "KIS OAuth secret ARN (수동 주입: {\"app_key\":\"...\",\"app_secret\":\"...\"})"
  value       = module.data_pipeline.kis_secret_arn
}

output "data_pipeline_dart_secret_arn" {
  description = "OpenDART API key secret ARN (수동 주입: {\"apikey\":\"...\"})"
  value       = module.data_pipeline.dart_secret_arn
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

# ── UI 배포용 값 (GitHub repo vars — deploy-ui.yml 콜러가 with 로 전달) ──
output "tenant_console_ui_bucket" {
  description = "→ vars.TENANT_CONSOLE_UI_BUCKET"
  value       = module.tenant_console_site.bucket_name
}

output "tenant_console_ui_distribution_id" {
  description = "→ vars.TENANT_CONSOLE_UI_DISTRIBUTION_ID"
  value       = module.tenant_console_site.distribution_id
}

output "super_admin_ui_bucket" {
  description = "→ vars.SUPER_ADMIN_UI_BUCKET"
  value       = module.super_admin_site.bucket_name
}

output "super_admin_ui_distribution_id" {
  description = "→ vars.SUPER_ADMIN_UI_DISTRIBUTION_ID"
  value       = module.super_admin_site.distribution_id
}
