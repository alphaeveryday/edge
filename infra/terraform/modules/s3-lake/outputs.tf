output "bucket_name" {
  value = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}

output "kms_key_arn" {
  description = "버킷 SSE-KMS 가 쓰는 키 ARN. 레이크에 R/W 하는 역할의 KMS 권한 대상."
  value       = data.aws_kms_alias.s3.target_key_arn
}
