output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.this.family
}

output "security_group_id" {
  value = aws_security_group.this.id
}
