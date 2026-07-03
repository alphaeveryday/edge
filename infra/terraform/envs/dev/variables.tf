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
  description = "데이터 레이크 버킷 이름(전역 유일). 파이프라인 수집→분석 소비의 공유 데이터(뉴스·가격·재무) 저장소. 계정 ID 접미사로 충돌 회피"
  type        = string
  default     = "edge-data-lake-393229433969"
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

variable "widget_domain" {
  description = "widget-ui(임베드 위젯) CDN 도메인. prod 는 widget.edgesignal.dev 로."
  type        = string
  default     = "widget-dev.edgesignal.dev"
}

variable "console_domain" {
  description = "tenant-console-ui(콘솔 SPA) CDN 도메인. prod 는 app.edgesignal.dev 로."
  type        = string
  default     = "console-dev.edgesignal.dev"
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

variable "pipeline_image" {
  description = "news-pipeline 배치 이미지 URI(:태그 포함). foundation 의 edge/pipeline 에 push 후 그 URI."
  type        = string
}

variable "pipeline_contact_email" {
  description = "파이프라인이 외부 뉴스 소스 접근 시 밝히는 연락 이메일"
  type        = string
  default     = "asm.alphaeveryday@gmail.com"
}

variable "pipeline_alarm_email" {
  description = "파이프라인 실패 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만."
  type        = string
  default     = null
}

variable "admin_domain" {
  description = "super-admin-ui(운영 콘솔 SPA) CDN 도메인. prod 는 admin.edgesignal.dev 로."
  type        = string
  default     = "admin-dev.edgesignal.dev"
}
