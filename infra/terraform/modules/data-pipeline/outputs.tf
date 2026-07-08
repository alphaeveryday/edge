output "state_machine_arn" {
  description = "raw ingest 상태머신 ARN"
  value       = aws_sfn_state_machine.this.arn
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
