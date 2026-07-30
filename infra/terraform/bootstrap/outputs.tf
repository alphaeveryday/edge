output "state_bucket_name" {
  description = "envs/*/backend.tf 의 bucket 에 넣을 값"
  value       = aws_s3_bucket.state.id
}

output "region" {
  description = "envs/*/backend.tf 의 region 에 넣을 값"
  value       = var.region
}
