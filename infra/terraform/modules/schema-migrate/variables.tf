variable "name" {
  description = "리소스 접두어 (예: edge-dev-schema-migrate)"
  type        = string
}

variable "region" {
  description = "awslogs 및 ECR 리전"
  type        = string
}

variable "vpc_id" {
  description = "마이그레이션 task SG 를 둘 VPC"
  type        = string
}

variable "flyway_url" {
  description = "JDBC URL (평문). 예: jdbc:postgresql://<rds-endpoint>/<db>"
  type        = string
}

variable "flyway_user" {
  description = "DB 사용자 (평문, RDS master_username)"
  type        = string
}

variable "flyway_password_secret_arn" {
  description = "DB 비밀번호 시크릿의 base ARN(접미사 없이). RDS 관리형 시크릿({username,password} JSON)을 넘긴다. 모듈이 valueFrom 에 ':password::' 를 붙이고, IAM 정책 Resource 는 이 base ARN 을 그대로 쓴다."
  type        = string
}

variable "db_security_group_id" {
  description = "RDS 의 SG. 이 모듈이 egress 5432 를 이 SG 로 여는 게 아니라, 호출부(env)가 이 모듈의 SG 를 RDS 인바운드로 허용한다(순환 의존 회피)."
  type        = string
  default     = null
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "cpu_architecture" {
  description = "빌드하는 마이그레이션 이미지 아키텍처와 일치"
  type        = string
  default     = "X86_64"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "ecr_repository_url" {
  description = "마이그레이션 이미지 ECR repo URL (foundation 의 edge/schema-migrate). 태스크 정의 placeholder 이미지에 사용."
  type        = string
}

variable "ecr_repository_arn" {
  description = "위 repo ARN. env 가 github-oidc-deploy 의 push 권한 대상으로 참조(모듈 외부에서 씀)."
  type        = string
}
