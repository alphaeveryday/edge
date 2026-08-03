output "state_machine_arn" {
  description = "파이프라인 상태머신 ARN (raw → normalize → feature → analyze)"
  value       = aws_sfn_state_machine.this.arn
}

output "analysis_task_definition_family" {
  description = "analyze 페이즈 ECS task definition family — 특정일 수동 재실행(ecs run-task --trade-date) 대상"
  value       = aws_ecs_task_definition.analysis.family
}

output "task_definition_families" {
  description = "data-pipeline ECS task definition families by vendor"
  value       = { for key, task_definition in aws_ecs_task_definition.this : key => task_definition.family }
}

output "task_definition_arns" {
  value = { for key, task_definition in aws_ecs_task_definition.this : key => task_definition.arn }
}

output "security_group_id" {
  value = aws_security_group.task.id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

output "log_group_arn" {
  value = aws_cloudwatch_log_group.this.arn
}

output "lake_bucket_name" {
  value = var.lake_bucket_name
}

output "fmp_secret_arn" {
  value = aws_secretsmanager_secret.fmp.arn
}

output "kis_secret_arn" {
  value = aws_secretsmanager_secret.kis.arn
}

output "dart_secret_arn" {
  value = aws_secretsmanager_secret.dart.arn
}

output "minute_service_arns" {
  description = "1분 상주 서비스 3종 ARN(ALPHA-711) — CD(deploy-data-pipeline.yml)의 force-new-deployment 대상"
  value       = [for s in aws_ecs_service.minute : s.id]
}

output "minute_service_names" {
  description = "1분 상주 서비스 이름 — CD 가 존재 확인 후 롤링 재배포에 쓴다"
  value       = [for s in aws_ecs_service.minute : s.name]
}
