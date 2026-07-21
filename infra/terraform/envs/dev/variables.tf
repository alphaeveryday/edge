variable "region" {
  description = "배포 region"
  type        = string
}

variable "vpc_cidr" {
  description = "dev VPC CIDR"
  type        = string
  default     = "10.0.0.0/16"
}

variable "super_admin_api_image" {
  description = "super-admin-api ECR 이미지 URI(:태그 포함)"
  type        = string
}

variable "route53_zone_name" {
  description = "기존 Route53 호스팅 영역 이름(참조용)"
  type        = string
  default     = "edgesignal.dev"
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

# ⚠️ null 이면 SNS 구독 리소스가 count=0 으로 **아예 안 생겨**, NotifyFailure·CloudWatch 알람이
# 구독자 없는 토픽에 publish 한다 — 즉 실패가 아무 데도 안 알려진다. 여기 주소가 있어야
# data-pipeline 의 실패 통보가 실제로 사람에게 닿는다(ALPHA-389 에서 라이브 실측으로 발견:
# 토픽 구독자 0). 정제가 run 스코프로 바뀌면서 실패 런의 raw 를 사람이 명시적으로 재처리해야
# 하는데, 그 절차의 유일한 트리거가 이 알림이다.
variable "pipeline_alarm_email" {
  description = "파이프라인 실패 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만(=알림 유실)."
  type        = string
  default     = "asm.alphaeveryday@gmail.com"
}

variable "admin_domain" {
  description = "super-admin-ui(운영 콘솔 SPA) CDN 도메인. prod 는 admin.edgesignal.dev 로."
  type        = string
  default     = "admin-dev.edgesignal.dev"
}
