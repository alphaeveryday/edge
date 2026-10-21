output "task_definition_family" {
  value = aws_ecs_task_definition.this.family
}

output "security_group_id" {
  value = aws_security_group.this.id
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}
