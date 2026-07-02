variable "region" {
  description = "배포 region"
  type        = string
}

variable "vpc_cidr" {
  description = "dev VPC CIDR"
  type        = string
  default     = "10.0.0.0/16"
}

variable "widget_api_image" {
  description = "widget-api ECR 이미지 URI(:태그 포함)"
  type        = string
}

variable "tenant_console_api_image" {
  description = "tenant-console-api ECR 이미지 URI(:태그 포함)"
  type        = string
}

variable "super_admin_api_image" {
  description = "super-admin-api ECR 이미지 URI(:태그 포함)"
  type        = string
}

variable "lake_bucket_name" {
  description = "데이터 레이크 버킷 이름(전역 유일). 계정 ID 접미사로 충돌 회피 — market-data 버킷과 동일 패턴"
  type        = string
  default     = "edge-dev-lake-393229433969"
}

variable "alb_allowed_cidrs" {
  description = "임시 검증 ALB 인바운드 허용 CIDR"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "route53_zone_name" {
  description = "기존 Route53 호스팅 영역 이름(참조용)"
  type        = string
  default     = "edgesignal.dev"
}

variable "edge_domain" {
  description = "엣지 ALB 에 붙일 도메인"
  type        = string
  default     = "edge-dev.edgesignal.dev"
}

variable "github_org_repo" {
  description = "OIDC 로 배포 역할을 assume 할 GitHub repo (owner/repo)"
  type        = string
  default     = "alphaeveryday/edge"
}

variable "create_github_oidc_provider" {
  description = "계정에 GitHub OIDC provider 가 없으면 true. 이미 있으면 false 로 두고 github_oidc_provider_arn 에 기존 provider ARN 을 넘긴다."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "create_github_oidc_provider=false 일 때 사용할 기존 GitHub OIDC provider ARN. true 면 무시된다."
  type        = string
  default     = null
}
