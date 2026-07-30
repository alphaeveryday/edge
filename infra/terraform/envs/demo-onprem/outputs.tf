output "demo_instance_id" {
  description = "데모 박스 인스턴스 ID (SSM SendCommand 대상)"
  value       = module.demo_onprem.instance_id
}

output "demo_instance_arn" {
  description = "데모 박스 ARN — CD(deploy-demo-onprem.yml) SSM 권한 스코프용"
  value       = module.demo_onprem.instance_arn
}

output "demo_public_ip" {
  description = "데모 박스 공개 IP"
  value       = module.demo_onprem.public_ip
}

output "console_url" {
  description = "데모 검수 콘솔 URL (CloudFront, 로그인 게이트 — ALPHA-627)"
  value       = "https://${var.console_domain}"
}

output "mts_url" {
  description = "가상 MTS 페이지 URL"
  value       = module.mts_site.url
}

output "mts_bucket" {
  description = "MTS S3 버킷 이름 → vars.DEMO_MTS_BUCKET (deploy-demo-onprem.yml s3 sync 대상)"
  value       = module.mts_site.bucket_name
}

output "mts_distribution_id" {
  description = "MTS CloudFront 배포 ID → vars.DEMO_MTS_DISTRIBUTION_ID (CD 무효화)"
  value       = module.mts_site.distribution_id
}

output "deploy_role_arn" {
  description = "데모 배포 역할 ARN → vars.AWS_DEMO_DEPLOY_ROLE_ARN (deploy-demo-onprem.yml OIDC)"
  value       = aws_iam_role.deploy.arn
}

output "cert_parameter_name" {
  description = "데모 mTLS cert SSM SecureString 파라미터 이름 — 운영자가 CLI(put-parameter)로 여기 생성·주입"
  value       = local.cert_param_name
}
