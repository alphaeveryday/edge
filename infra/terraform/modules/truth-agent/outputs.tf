output "store_bucket" {
  description = "비공개 저장소 버킷 (사건 원본·실행 보고 runs/)"
  value       = aws_s3_bucket.store.bucket
}

output "runtime_secret_name" {
  description = "값을 수동으로 넣을 시크릿 이름"
  value       = aws_secretsmanager_secret.runtime.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

output "store_bucket_arn" {
  description = "비공개 저장소 ARN (조사 에이전트가 같은 버킷의 말뭉치·급등주 상태를 쓴다)"
  value       = aws_s3_bucket.store.arn
}

output "runtime_secret_arn" {
  description = "실행 키 시크릿 ARN (조사 에이전트가 같은 키를 쓴다)"
  value       = aws_secretsmanager_secret.runtime.arn
}
