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
