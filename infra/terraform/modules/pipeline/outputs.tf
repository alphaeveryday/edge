output "state_machine_arn" {
  description = "파이프라인 상태머신 ARN (수동 검증 실행에 사용)"
  value       = aws_sfn_state_machine.this.arn
}

output "security_group_id" {
  description = "배치 task SG. 호출부(env)가 RDS 인바운드로 허용한다."
  value       = aws_security_group.task.id
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "curated_bucket" {
  value = aws_s3_bucket.curated.bucket
}

output "fmp_secret_arn" {
  description = "FMP 키 시크릿 ARN (apply 후 값 수동 주입)"
  value       = aws_secretsmanager_secret.fmp.arn
}

output "openai_secret_arn" {
  description = "OpenAI 키 시크릿 ARN (apply 후 값 수동 주입)"
  value       = aws_secretsmanager_secret.openai.arn
}

output "alarms_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "pipeline_task_definition_arn" {
  value = aws_ecs_task_definition.pipeline.arn
}

output "inference_task_definition_arn" {
  value = aws_ecs_task_definition.inference.arn
}
