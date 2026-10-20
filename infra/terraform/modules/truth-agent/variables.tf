variable "name" {
  description = "리소스 접두어 (예: edge-dev-truth-agent). 태스크 정의 family·로그 그룹·버킷 이름에 쓴다."
  type        = string
}

variable "region" {
  description = "awslogs·ARN 리전"
  type        = string
}

variable "vpc_id" {
  description = "태스크 보안그룹을 둘 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "태스크를 띄울 private 서브넷 (NAT 로 모델·웹 검색 API 에 나간다)"
  type        = list(string)
}

variable "cluster_arn" {
  description = "단발 태스크를 띄울 ECS 클러스터 (worker)"
  type        = string
}

variable "image" {
  description = "truth-agent 이미지 URI (:태그 포함). 매 실행이 이 태그를 새로 pull 한다."
  type        = string
}

variable "lake_bucket_name" {
  description = "뉴스 레이크 버킷 이름 (1분 원본·정제본을 읽는다)"
  type        = string
}

variable "lake_bucket_arn" {
  description = "뉴스 레이크 버킷 ARN (읽기 권한 범위: 뉴스 두 경로)"
  type        = string
}

variable "landing_bucket_name" {
  description = "랜딩 사이트 버킷 이름. 공개 데이터를 truth/data/ 에 쓴다."
  type        = string
}

variable "landing_bucket_arn" {
  description = "랜딩 사이트 버킷 ARN (쓰기 권한 범위: truth/data/*)"
  type        = string
}

variable "alarm_topic_arn" {
  description = "태스크 실패를 보낼 SNS 토픽"
  type        = string
}

variable "scheduler_dlq_arn" {
  description = "스케줄러가 RunTask 제출에 실패한 이벤트를 보낼 큐"
  type        = string
}

variable "hourly_schedule_expression" {
  description = "사건 갱신 시각 (Asia/Seoul). 정시 직후 1분 수집분이 레이크에 쌓인 뒤로 둔다."
  type        = string
  default     = "cron(5 * * * ? *)"
}

variable "discover_schedule_expression" {
  description = "새 사건 찾기 시각 (Asia/Seoul). 아침 정제본(08:10) 적재 뒤, 매시 실행(:05)과 겹치지 않는 시각."
  type        = string
  default     = "cron(40 8 * * ? *)"
}

variable "schedule_state" {
  description = "ENABLED 또는 DISABLED. 키를 넣기 전·장애 대응 중에는 DISABLED 로 멈춘다."
  type        = string
  default     = "ENABLED"
  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.schedule_state)
    error_message = "schedule_state 는 ENABLED 또는 DISABLED 다."
  }
}
