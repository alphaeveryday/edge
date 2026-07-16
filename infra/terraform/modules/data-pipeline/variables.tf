variable "name" {
  description = "리소스 접두어 (예: edge-dev-data-pipeline)"
  type        = string
}

variable "region" {
  description = "awslogs·스케줄러 리전"
  type        = string
}

variable "vpc_id" {
  description = "data-pipeline task SG 를 둘 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "task 를 띄울 private 서브넷(NAT 로 외부 API/ECR 도달)"
  type        = list(string)
}

variable "cluster_arn" {
  description = "배치를 실행할 ECS 클러스터 ARN"
  type        = string
}

variable "image" {
  description = "data-pipeline 컨테이너 이미지 URI(:태그 포함)"
  type        = string
}

variable "lake_bucket_name" {
  description = "raw/canonical/curated prefix 를 담는 lake bucket 이름"
  type        = string
}

variable "lake_bucket_arn" {
  description = "raw/canonical/curated prefix 를 담는 lake bucket ARN"
  type        = string
}

# ── DB (edge RDS, Cloud Event Store) ────────────────────
# 적재 스텝(load-*)만 쓴다. 접속정보는 평문 env, 비밀번호만 RDS 관리형 시크릿에서 주입
# (analysis-engine 모듈과 같은 관례). 단 env 이름은 data-pipeline 의 설정 네임스페이스를
# 따른다 — DATA_PIPELINE_DB__*(DbConfig). PG* 가 아니다.
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
  description = "DB 비밀번호 시크릿 base ARN. RDS 관리형 시크릿({username,password} JSON). 모듈이 ':password::' 를 붙여 DATA_PIPELINE_DB__PASSWORD 로 주입."
  type        = string
}

# ── DeepSeek LLM (tag-news) ─────────────────────────────
# analysis-engine 과 **같은 시크릿을 공유**하므로 그릇을 이 모듈이 소유하지 않는다(두 모듈이
# 한 리소스를 동시에 소유할 수 없다) — 호출부가 data 로 조회해 ARN 을 넘긴다.
variable "deepseek_secret_arn" {
  description = "DeepSeek API 키 시크릿 base ARN({\"api_key\":\"...\"} JSON). 모듈이 ':api_key::' 를 붙여 LLM_API_KEY 로 주입."
  type        = string
}

variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  type    = number
  default = 2048
}

variable "cpu_architecture" {
  description = "이미지 아키텍처와 일치"
  type        = string
  default     = "X86_64"
}

variable "schedule_expression" {
  description = "EventBridge Scheduler cron. 기본은 평일 미 동부 16:10(장 마감 후)."
  type        = string
  default     = "cron(10 16 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  type    = string
  default = "America/New_York"
}

variable "schedule_state" {
  description = "검증 동안은 DISABLED. 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
}

variable "alarm_email" {
  description = "raw ingest 실패 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "state_machine_timeout_seconds" {
  type    = number
  default = 21600
}
