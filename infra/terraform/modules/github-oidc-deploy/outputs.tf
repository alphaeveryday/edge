output "role_arn" {
  description = "GitHub Actions 가 assume 할 배포 역할 ARN (vars.AWS_ROLE_ARN 로 설정)"
  value       = aws_iam_role.this.arn
}

output "oidc_provider_arn" {
  description = "생성했거나 참조한 GitHub OIDC provider ARN (prod 역할에서 재사용)"
  value       = local.oidc_provider_arn
}
