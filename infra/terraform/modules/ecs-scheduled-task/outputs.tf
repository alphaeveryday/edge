output "ecr_repository_url" {
  description = "배포 파이프라인이 이미지를 push 할 저장소 URL"
  value       = aws_ecr_repository.this.repository_url
}

output "ecr_repository_arn" {
  value = aws_ecr_repository.this.arn
}

output "task_definition_family" {
  value = aws_ecs_task_definition.this.family
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "security_group_id" {
  value = aws_security_group.this.id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}
