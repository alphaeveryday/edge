variable "name" {
  description = "IAM 역할 이름 (예: edge-dev-gha-schema-migrate)"
  type        = string
}

variable "github_org_repo" {
  description = "OIDC subject 로 신뢰할 GitHub repo (owner/repo). 예: alphaeveryday/edge"
  type        = string
}

variable "github_environments" {
  description = "이 역할을 assume 할 수 있는 GitHub Actions environment 이름 목록. job 이 `environment:` 를 참조하면 OIDC 토큰 sub 가 `repo:owner/repo:environment:NAME` 형태가 되므로, 브랜치 ref 가 아니라 environment 로 신뢰한다. dev 역할은 [\"development\"], prod 역할은 [\"production\"]."
  type        = list(string)
}

variable "create_oidc_provider" {
  description = "계정에 GitHub OIDC provider 가 아직 없으면 true. 이미 있으면 false 로 두고 oidc_provider_arn 을 넘긴다."
  type        = bool
  default     = true
}

variable "oidc_provider_arn" {
  description = "create_oidc_provider=false 일 때 사용할 기존 provider ARN"
  type        = string
  default     = null
}

variable "ecr_repository_arn" {
  description = "이미지를 push 할 마이그레이션 ECR 저장소 ARN"
  type        = string
}

variable "ecs_cluster_arn" {
  description = "RunTask 대상 클러스터 ARN (condition 으로 제한)"
  type        = string
}

variable "task_definition_family" {
  description = "RunTask/Register 대상 task 정의 family"
  type        = string
}

variable "pass_role_arns" {
  description = "PassRole 을 허용할 역할 ARN 목록 (마이그레이션 task 의 execution·task 역할)"
  type        = list(string)
}

variable "log_group_arn" {
  description = "마이그레이션 로그 그룹 ARN (워크플로가 로그를 읽기 위함)"
  type        = string
}

# ── 앱 배포(deploy-app.yml) 확장 — 모두 옵션(기본 빈 리스트, 비우면 앱 배포 권한 없음) ──
# 이 역할은 schema-migrate 와 앱 배포가 공유한다(같은 AWS_DEPLOY_ROLE_ARN). 아래를 채우면
# 마이그레이션 권한에 더해 앱 이미지 push·서비스 롤링 배포·앱 역할 PassRole 을 부여한다.
variable "app_ecr_repository_arns" {
  description = "앱 배포가 이미지를 push 할 ECR 저장소 ARN 목록 (edge/<app>). 비우면 앱 ECR 권한 없음."
  type        = list(string)
  default     = []
}

variable "app_service_arns" {
  description = "앱 롤링 배포(ecs:UpdateService/DescribeServices) 대상 ECS 서비스 ARN 목록. 비우면 권한 없음."
  type        = list(string)
  default     = []
}

variable "app_pass_role_arns" {
  description = "앱 task 정의가 참조하는 execution·task 역할 ARN 목록 (PassRole 추가 허용)."
  type        = list(string)
  default     = []
}

# ── UI CD(deploy-ui.yml) 확장 — 모두 옵션(기본 빈 리스트, 비우면 UI 배포 권한 없음) ──
# 정적 프론트를 S3 에 sync 하고 CloudFront 를 무효화하는 권한. 같은 배포 역할을 공유한다.
variable "ui_bucket_arns" {
  description = "UI 산출물을 sync 할 S3 버킷 ARN 목록. 버킷(ListBucket)·객체(Put/Delete)에 스코프. 비우면 권한 없음."
  type        = list(string)
  default     = []
}

variable "ui_distribution_arns" {
  description = "UI 배포 후 무효화(cloudfront:CreateInvalidation/GetInvalidation)할 CloudFront 배포 ARN 목록. 비우면 권한 없음."
  type        = list(string)
  default     = []
}
