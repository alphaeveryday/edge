output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.this.arn
}

output "sns_topic_arn" {
  description = "실패 알림 토픽 — 이메일 등 구독은 수동으로 건다"
  value       = aws_sns_topic.alarms.arn
}

output "security_group_id" {
  description = "RDS 인바운드 허용 대상으로 호출부(env)가 참조"
  value       = aws_security_group.task.id
}

output "pipeline_task_definition_arn" {
  value = aws_ecs_task_definition.pipeline.arn
}

output "inference_task_definition_arn" {
  value = aws_ecs_task_definition.inference.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.task.name
}

output "schedule_name" {
  value = aws_scheduler_schedule.daily.name
}
