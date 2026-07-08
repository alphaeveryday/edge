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
  description = "raw 수집물을 저장할 기존 S3 bucket 이름"
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
