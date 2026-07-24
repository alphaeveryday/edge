output "lake_bucket" {
  description = "active pipeline lake bucket 이름. raw/canonical/curated prefix 를 함께 담는다."
  value       = aws_s3_bucket.lake.bucket
}

output "lake_bucket_arn" {
  description = "active pipeline lake bucket ARN."
  value       = aws_s3_bucket.lake.arn
}
