output "state_machine_arn" {
  description = "analysis-engine 상태머신 ARN"
  value       = aws_sfn_state_machine.this.arn
}

output "task_definition_family" {
  description = "analysis-engine ECS task definition family"
  value       = aws_ecs_task_definition.this.family
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.this.arn
}

output "security_group_id" {
  description = "배치 task SG. 호출부(env)가 RDS 인바운드로 허용한다."
  value       = aws_security_group.task.id
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
