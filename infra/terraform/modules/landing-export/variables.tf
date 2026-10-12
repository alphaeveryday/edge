variable "name" {
  description = "함수·역할·일정 이름 (예: edge-dev-landing-export)"
  type        = string
}

variable "api_url" {
  description = "분석 v2 조회 API 주소 (module.analysis_v2.api_url). 호출 권한의 API ID 도 여기서 뽑는다."
  type        = string
}

variable "bucket_name" {
  description = "랜딩 사이트 버킷 이름. outlook/<코드>.json 을 쓴다."
  type        = string
}

variable "bucket_arn" {
  description = "랜딩 사이트 버킷 ARN (쓰기 권한 범위: outlook/*)"
  type        = string
}

variable "etf_codes" {
  description = "내보낼 ETF 코드 — 전망 배치 대상과 같은 목록을 넘긴다."
  type        = list(string)
}

variable "alarm_topic_arn" {
  description = "내보내기 실패 알람을 보낼 SNS 토픽"
  type        = string
}

variable "scheduler_dlq_arn" {
  description = "스케줄러가 호출에 실패한 이벤트를 보낼 큐"
  type        = string
}

variable "schedule_expression" {
  description = "실행 시각 (Asia/Seoul). 전망 배치 마감(08:00) 과 같은 시각이 기본이다."
  type        = string
  default     = "cron(0 8 * * ? *)"
}
