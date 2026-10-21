variable "name" {
  description = "task 패밀리·로그 그룹·역할·스케줄 이름 (배포 워크플로의 앱 이름과 같아야 한다)"
  type        = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "태스크를 띄울 private 서브넷"
  type        = list(string)
}

variable "cluster_arn" {
  type = string
}

variable "container_image" {
  description = "task 정의의 baseline 이미지. 배포가 매번 옮기고 ECR 이 보존하는 움직이는 태그여야 한다. 이후 리비전은 배포 워크플로가 등록한다"
  type        = string
}

variable "cpu" {
  type    = string
  default = "512"
}

variable "memory" {
  type    = string
  default = "1024"
}

variable "cpu_architecture" {
  type    = string
  default = "X86_64"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "environment" {
  description = "평문 환경변수 map"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "시크릿 환경변수 map (값은 Secrets Manager ARN, JSON 키는 arn:...:json-key:: 형식)"
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "실행 역할에 GetSecretValue 를 허용할 시크릿 기반 ARN 목록"
  type        = list(string)
  default     = []
}

variable "schedule_expression" {
  description = "EventBridge Scheduler 식 (Asia/Seoul)"
  type        = string
}

variable "schedule_enabled" {
  description = "false 면 스케줄을 DISABLED 로 만든다(수동 RunTask 는 가능)"
  type        = bool
}

variable "max_event_age_seconds" {
  description = "RunTask 제출 재시도 상한. 다음 주기와 겹치지 않게 주기보다 짧게"
  type        = number
  default     = 300
}

variable "alarm_topic_arn" {
  description = "제출 실패(DLQ 도착)·실행 실패(종료 코드 ≠ 0) 알림 SNS 토픽"
  type        = string
}

variable "success_log_pattern" {
  description = "성공 완료 1회를 나타내는 CloudWatch Logs 필터 패턴. null 이면 하트비트 알람 없음"
  type        = string
  default     = null
}

variable "heartbeat_minutes" {
  description = "이 시간 동안 성공 완료가 0회면 알람. 주기의 몇 배로 둬 한두 번의 실패는 실행 실패 알람에 맡긴다"
  type        = number
  default     = 30
}
