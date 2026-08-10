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

variable "tenant_sync_api_image" {
  description = "tenant-sync-api ECR 이미지 URI(:태그 포함). TF 소유 baseline — 실행 태그는 CD 소유"
  type        = string
}

variable "sync_domain" {
  description = "Sync 채널 고정 FQDN — 온프렘 sync-agent 의 outbound-Pull 단일 목적지. prod 는 sync.edgesignal.dev 로."
  type        = string
  default     = "sync-dev.edgesignal.dev"
}

variable "sync_mtls_trust_store_arn" {
  description = "sync ALB mTLS trust store ARN. null=아직 mTLS 미적용(2단계 — CA·번들 준비 후 주입, ALPHA-447). 주입 전까지 sync 엔드포인트는 공개 도달"
  type        = string
  default     = null
}

variable "route53_zone_name" {
  description = "기존 Route53 호스팅 영역 이름(참조용)"
  type        = string
  default     = "edgesignal.dev"
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

# ⚠️ null 이면 SNS 구독 리소스가 count=0 으로 **아예 안 생겨**, NotifyFailure·CloudWatch 알람이
# 구독자 없는 토픽에 publish 한다 — 즉 실패가 아무 데도 안 알려진다. 여기 주소가 있어야
# data-pipeline 의 실패 통보가 실제로 사람에게 닿는다(ALPHA-389 에서 라이브 실측으로 발견:
# 토픽 구독자 0). 정제가 run 스코프로 바뀌면서 실패 런의 raw 를 사람이 명시적으로 재처리해야
# 하는데, 그 절차의 트리거가 이 알림이다.
# ⚠️ ALPHA-919 이후로는 RDS 메모리 고갈 경보도 같은 토픽·같은 구독에 달렸다. 여기를 null 로
# 두면 파이프라인 실패 통보만이 아니라 **DB 가 죽는다는 통보까지** 함께 사라진다 — 그게
# 2026-08-10 사고(하루 세 번 다운, 아침 09:30 경고 신호를 아무도 못 봄)의 실패 모드 그대로다.
variable "pipeline_alarm_email" {
  description = "파이프라인 실패·RDS 경보 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만(=알림 유실)."
  type        = string
  default     = "asm.alphaeveryday@gmail.com"
}

variable "admin_domain" {
  description = "super-admin-ui(운영 콘솔 SPA) CDN 도메인. prod 는 admin.edgesignal.dev 로."
  type        = string
  default     = "admin-dev.edgesignal.dev"
}

variable "admin_api_domain" {
  description = "super-admin-api 공개 엣지 ALB 도메인(ADR-0034 — UI 도메인 admin-dev 와 짝). prod 는 admin-api.edgesignal.dev 로."
  type        = string
  default     = "admin-api-dev.edgesignal.dev"
}
