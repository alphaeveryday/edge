variable "name" {
  description = "리소스 접두어 (예: edge-dev-pipeline)"
  type        = string
}

variable "region" {
  description = "awslogs·ECR·스케줄러 리전"
  type        = string
}

variable "vpc_id" {
  description = "파이프라인 task SG 를 둘 VPC (edge VPC — A안 통합)"
  type        = string
}

variable "subnet_ids" {
  description = "task 를 띄울 private 서브넷(NAT 로 외부 API 도달). edge private 서브넷."
  type        = list(string)
}

variable "cluster_arn" {
  description = "배치를 실행할 ECS 클러스터 ARN (edge-dev-worker)"
  type        = string
}

variable "image" {
  description = "파이프라인 컨테이너 이미지 URI(:태그 포함). 이관 초기엔 기존 news-pipeline ECR 이미지를 재사용."
  type        = string
}

# ── DB (edge RDS, A안 통합) ─────────────────────────────
# edge 관례: 접속정보는 평문 env(NEWSDB_*), 비밀번호만 RDS 관리형 시크릿에서 주입(PGPW).
variable "db_host" {
  description = "edge RDS host (address, 포트 제외)"
  type        = string
}

variable "db_port" {
  description = "edge RDS 포트"
  type        = number
  default     = 5432
}

variable "db_name" {
  description = "edge RDS 데이터베이스 이름"
  type        = string
}

variable "db_user" {
  description = "edge RDS 사용자 (평문)"
  type        = string
}

variable "db_password_secret_arn" {
  description = "DB 비밀번호 시크릿 base ARN. RDS 관리형 시크릿({username,password} JSON). 모듈이 ':password::' 를 붙여 PGPW 로 주입."
  type        = string
}

# ── 태스크 자원 ─────────────────────────────────────────
variable "pipeline_cpu" {
  type    = number
  default = 2048
}

variable "pipeline_memory" {
  type    = number
  default = 4096
}

variable "inference_cpu" {
  type    = number
  default = 4096
}

variable "inference_memory" {
  type    = number
  default = 16384
}

variable "cpu_architecture" {
  description = "이미지 아키텍처와 일치 (news-pipeline 이미지는 amd64)"
  type        = string
  default     = "X86_64"
}

# ── 앱 설정 (CDK 태스크 정의 값 그대로) ─────────────────
variable "contact_email" {
  description = "외부 뉴스 소스 크롤링 시 밝히는 연락 이메일 (User-Agent 등)"
  type        = string
}

# ── 스케줄 ──────────────────────────────────────────────
variable "schedule_expression" {
  description = "EventBridge Scheduler cron. CDK 와 동일: 평일 미 동부 16:05(장 마감 후)."
  type        = string
  default     = "cron(5 16 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  type    = string
  default = "America/New_York"
}

variable "schedule_state" {
  description = "이관 검증 동안은 DISABLED(이중 실행 방지). 컷오버 시 ENABLED 로."
  type        = string
  default     = "DISABLED"
}

# ── 알람 ────────────────────────────────────────────────
variable "alarm_email" {
  description = "파이프라인 실패 알림을 받을 이메일. null 이면 구독 없이 토픽만 생성."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 14
}
