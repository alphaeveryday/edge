variable "name" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "cluster_arn" { type = string }
variable "db_security_group_id" { type = string }
variable "bucket_name" { type = string }
variable "image" { type = string }
variable "deploy_role_name" { type = string }
variable "operator_arn" { type = string }
variable "api_image" { type = string }
variable "api_client_role_names" {
  type    = list(string)
  default = []
}
variable "analysis_slots" {
  type        = number
  default     = 1
  description = "동시에 도는 분석 수의 상한. 워커가 이 수의 슬롯 중 하나를 잡고 시작하며, 전망 배치의 동시 수도 이 값이다. 올리기 전에 writer 역할 연결 한도(건당 3개)와 슬롯을 읽는 워커 이미지가 먼저 배포돼 있어야 한다 — 머지마다 마이그레이션·이미지·terraform 이 순서 없이 따로 적용된다."
  validation {
    condition     = var.analysis_slots >= 1 && floor(var.analysis_slots) == var.analysis_slots
    error_message = "analysis_slots 는 1 이상의 정수여야 한다."
  }
}
# 전망 배치(ALPHA-1142)
variable "outlook_etf_codes" {
  type        = list(string)
  description = "전망 배치 대상 ETF 코드. 수집 설정의 국내 ETF 목록에서 호출부가 읽어 넘긴다."
}
variable "outlook_schedule_expression" {
  type        = string
  description = "전망 배치 스케줄(Asia/Seoul). 예정 시각이 그대로 업무 기준시각이 된다. 당일 00:00~08:00 KST 사이여야 한다(전망 날짜 = 기준시각의 한국 날짜)."
}
variable "outlook_schedule_state" {
  type    = string
  default = "DISABLED"
}
variable "outlook_deadline_utc" {
  type        = string
  default     = "23:00:00Z"
  description = "저장 마감의 UTC 시각 부분. 23:00:00Z = 08:00 KST. 기준시각과 같은 UTC 날짜에 붙인다(기준시각이 09:00~다음 날 08:00 KST 사이여야 한다)."
}
variable "alarm_topic_arn" { type = string }
variable "scheduler_dlq_arn" { type = string }
