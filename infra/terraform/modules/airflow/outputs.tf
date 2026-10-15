output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.airflow.name
}

output "migrate_task_definition_family" {
  value = aws_ecs_task_definition.migrate.family
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}

output "subnet_ids" {
  description = "마이그레이션 one-off 태스크를 띄울 서브넷(deploy-airflow)"
  value       = var.subnet_ids
}

output "autoscaling_group_name" {
  value = aws_autoscaling_group.host.name
}

output "component_log_group_name" {
  value = aws_cloudwatch_log_group.components.name
}

output "task_log_group_name" {
  value = aws_cloudwatch_log_group.tasks.name
}

output "app_secret_arn" {
  value = aws_secretsmanager_secret.airflow.arn
}

output "verify_bucket" {
  value = var.verify_enabled ? aws_s3_bucket.verify[0].bucket : null
}

output "verify_security_group_id" {
  value = var.verify_enabled ? aws_security_group.verify[0].id : null
}
