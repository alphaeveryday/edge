output "bucket_name" {
  description = "빌드 산출물을 sync 할 S3 버킷 (CD: aws s3 sync ./dist s3://<이 값>)"
  value       = aws_s3_bucket.this.bucket
}

output "distribution_id" {
  description = "배포 후 CD 가 무효화할 CloudFront 배포 ID (aws cloudfront create-invalidation)"
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_domain_name" {
  description = "CloudFront 기본 도메인 (*.cloudfront.net)"
  value       = aws_cloudfront_distribution.this.domain_name
}

output "url" {
  description = "공개 URL"
  value       = "https://${var.domain_name}"
}

output "bucket_arn" {
  description = "S3 버킷 ARN — UI CD 배포 역할의 s3 sync 권한 스코프"
  value       = aws_s3_bucket.this.arn
}

output "distribution_arn" {
  description = "CloudFront 배포 ARN — UI CD 의 무효화(CreateInvalidation) 권한 스코프"
  value       = aws_cloudfront_distribution.this.arn
}

output "key_value_store_arn" {
  description = "key_value_store=true 일 때 KeyValueStore ARN — 값은 aws cloudfront-keyvaluestore put-key --kvs-arn <이 값> 으로 넣는다"
  value       = var.key_value_store ? aws_cloudfront_key_value_store.this[0].arn : null
}
