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
variable "price_queue_url" { type = string }
variable "price_queue_arn" { type = string }
variable "api_client_role_names" {
  type    = list(string)
  default = []
}
variable "analysis_slots" {
  type        = number
  default     = 1
  description = "동시에 도는 분석 수의 상한. 단건 워크플로의 자리 수(배치·API·가격변동 공통)이자 전망 배치 Map 의 동시 수다. 실행 ARN 을 받은 워커는 DB 단계마다 연결을 열고 닫으므로 분석당 장기 연결이 없다 — writer 역할 한도는 동시 시작 순간과 실행 제어 Lambda·로컬 대시보드 몫으로 정한다(tests/loadtest/analysis-v2/README.md)."
  validation {
    # 40 은 배치의 Inline Map 동시 반복 한계다. 그 위는 Distributed Map 이 필요해 별도 과제다.
    condition     = var.analysis_slots >= 1 && var.analysis_slots <= 40 && floor(var.analysis_slots) == var.analysis_slots
    error_message = "analysis_slots 는 1~40 의 정수여야 한다(Inline Map 동시 반복 한계)."
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
