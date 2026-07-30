output "task_definition_family" {
  description = "RunTask 에 쓸 task 정의 family"
  value       = aws_ecs_task_definition.this.family
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.this.arn
}

output "security_group_id" {
  description = "마이그레이션 task SG. 호출부(env)가 RDS 인바운드로 허용한다."
  value       = aws_security_group.this.id
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

output "log_group_arn" {
  value = aws_cloudwatch_log_group.this.arn
}
