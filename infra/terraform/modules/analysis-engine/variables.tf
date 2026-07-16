variable "name" {
  description = "리소스 접두어 (예: edge-dev-analysis-engine)"
  type        = string
}

variable "region" {
  description = "awslogs·스케줄러 리전 (컨테이너 AWS_REGION 으로도 주입)"
  type        = string
}

variable "vpc_id" {
  description = "analysis-engine task SG 를 둘 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "task 를 띄울 private 서브넷(NAT 로 DeepSeek API/ECR/S3 도달)"
  type        = list(string)
}

variable "cluster_arn" {
  description = "배치를 실행할 ECS 클러스터 ARN"
  type        = string
}

variable "image" {
  description = "analysis-engine 컨테이너 이미지 URI(:태그 포함)"
  type        = string
}

variable "lake_bucket_name" {
  description = "canonical 뉴스를 읽고 설명 결과를 쓰는 lake bucket 이름 (ALPHAMALE_LAKE_BUCKET)"
  type        = string
}

variable "lake_bucket_arn" {
  description = "lake bucket ARN (S3 IAM 스코프용)"
  type        = string
}

# ── DB (edge RDS, Cloud Event Store) ────────────────────
# edge 관례: 접속정보는 평문 env(PGHOST 등), 비밀번호만 RDS 관리형 시크릿에서 PGPASSWORD 로 주입.
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
  description = "DB 비밀번호 시크릿 base ARN. RDS 관리형 시크릿({username,password} JSON). 모듈이 ':password::' 를 붙여 PGPASSWORD 로 주입."
  type        = string
}

# ── DeepSeek LLM ────────────────────────────────────────
variable "deepseek_secret_arn" {
  description = "DeepSeek API 키 시크릿 base ARN({\"api_key\":\"...\"} JSON). 모듈이 ':api_key::' 를 붙여 DEEPSEEK_API_KEY 로 주입."
  type        = string
}

variable "deepseek_model" {
  description = "DEEPSEEK_MODEL 로 주입할 모델명"
  type        = string
  default     = "deepseek-chat"
}

# ── 앱 설정 ─────────────────────────────────────────────
variable "pg_schema" {
  description = "PGSCHEMA — Cloud Event Store 스키마"
  type        = string
  default     = "public"
}

variable "etf_ticker" {
  description = "ALPHAMALE_ETF_TICKER — 대상 ETF (KODEX 반도체)"
  type        = string
  default     = "091160"
}

variable "result_s3_prefix" {
  description = "설명 결과를 쓰는 lake bucket 내 object key prefix(끝에 / 포함). env(ALPHAMALE_RESULT_S3_PREFIX)와 PutObject IAM 스코프에 함께 쓴다."
  type        = string
  default     = "operations_archive/etf_explanations/"
}

variable "release_bundle_version" {
  description = "ALPHAMALE_RELEASE_BUNDLE_VERSION — explanation_run 번들 고정. null 이면 주입 안 함(앱이 S3 fallback)."
  type        = string
  default     = null
}

# ── 태스크 자원 ─────────────────────────────────────────
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

# ── 스케줄 ──────────────────────────────────────────────
variable "schedule_expression" {
  description = "EventBridge Scheduler cron. 기본은 평일 KRX 장 마감 후(16:00 Asia/Seoul)."
  type        = string
  default     = "cron(0 16 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  type    = string
  default = "Asia/Seoul"
}

variable "schedule_state" {
  description = "검증 동안은 DISABLED. 컷오버 시 ENABLED."
  type        = string
  default     = "DISABLED"
}

# ── 알람 / 로그 ─────────────────────────────────────────
variable "alarm_email" {
  description = "실패 알림 수신 이메일. null 이면 SNS 구독 없이 토픽만."
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
