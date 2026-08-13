output "state_machine_arn" {
  description = "파이프라인 상태머신 ARN (raw → normalize → feature → analyze)"
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

output "minute_service_arns" {
  description = "1분 상주 서비스 전체 ARN(ALPHA-711·719) — 배포 역할의 Describe/Update 대상. analysis-consumer 포함(빠지면 deploy-analysis-engine 롤링이 AccessDenied — 유예 중엔 조용히 스킵돼 옛 이미지가 돈다)"
  value       = concat([for s in aws_ecs_service.minute : s.id], [aws_ecs_service.analysis_consumer.id])
}

output "minute_service_names" {
  description = "1분 상주 서비스 이름 — CD 가 존재 확인 후 롤링 재배포에 쓴다"
  value       = [for s in aws_ecs_service.minute : s.name]
}

output "minute_queue_urls" {
  description = "1분 파이프라인 원큐 URL — 콘솔 SQS 제어면 관측 대상"
  value       = { for name, queue in aws_sqs_queue.minute : name => queue.url }
}

output "minute_queue_arns" {
  description = "1분 파이프라인 원큐 ARN — 콘솔 GetQueueAttributes IAM 대상"
  value       = { for name, queue in aws_sqs_queue.minute : name => queue.arn }
}

output "minute_dlq_urls" {
  description = "1분 파이프라인 DLQ URL — 콘솔 SQS 제어면 관측 대상"
  value       = { for name, queue in aws_sqs_queue.minute_dlq : name => queue.url }
}

output "minute_dlq_arns" {
  description = "1분 파이프라인 DLQ ARN — 콘솔 GetQueueAttributes IAM 대상"
  value       = { for name, queue in aws_sqs_queue.minute_dlq : name => queue.arn }
}

output "alarm_topic_arn" {
  description = "파이프라인 알람 SNS 토픽. 감시 대상이 다른 모듈에 있는 알람(예: RDS)도 이 토픽 하나로 모은다 — 알림 채널을 쪼개면 구독을 두 번 확인해야 한다"
  value       = aws_sns_topic.alarms.arn
}
