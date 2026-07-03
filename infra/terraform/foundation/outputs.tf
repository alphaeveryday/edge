output "zone_id" {
  value = aws_route53_zone.main.zone_id
}

output "wildcard_certificate_arn" {
  description = "ap-northeast-2 와일드카드 ACM ARN (ALB 등 리전 서비스용). env 는 data 로 조회해도 됨."
  value       = aws_acm_certificate_validation.wildcard.certificate_arn
}

output "wildcard_cdn_certificate_arn" {
  description = "us-east-1 와일드카드 ACM ARN (CloudFront 용)."
  value       = aws_acm_certificate_validation.wildcard_cdn.certificate_arn
}

output "github_oidc_provider_arn" {
  description = "→ env 의 github_oidc_provider_arn (create_github_oidc_provider=false 와 함께)"
  value       = aws_iam_openid_connect_provider.github.arn
}

output "ecr_repository_urls" {
  description = "이미지 레포 URL 맵 (name → url)"
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}
