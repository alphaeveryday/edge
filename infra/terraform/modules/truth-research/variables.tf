variable "name" {
  description = "리소스 접두어 (예: edge-dev-truth-research). 태스크 정의 family·로그 그룹·스케줄 이름에 쓴다."
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
  description = "태스크를 띄울 private 서브넷 (NAT 로 밖에 나간다)"
  type        = list(string)
}

variable "cluster_arn" {
  description = "단발 태스크를 띄울 ECS 클러스터 (worker)"
  type        = string
}

variable "image" {
  description = "truth-research 이미지 URI (:태그 포함). 매 실행이 이 태그를 새로 pull 한다."
  type        = string
}

variable "store_bucket_name" {
  description = "truth-agent 저장소 버킷 이름 (뉴스 말뭉치를 읽고 급등주 상태를 쓴다)"
  type        = string
}

variable "store_bucket_arn" {
  description = "truth-agent 저장소 버킷 ARN"
  type        = string
}

variable "runtime_secret_arn" {
  description = "truth-agent 실행 키 시크릿 ARN (같은 키를 쓴다)"
  type        = string
}

variable "landing_bucket_name" {
  description = "랜딩 사이트 버킷 이름. 공개 데이터를 truth/data/movers·logos 에 쓴다."
  type        = string
}

variable "landing_bucket_arn" {
  description = "랜딩 사이트 버킷 ARN"
  type        = string
}

variable "schedule_expression" {
  description = "실행 시각 (Asia/Seoul). 장중 매시 :35 — truth-agent 사건 갱신(:05)과 30분 어긋나고, 15:35 실행이 장 마감 종가를 잡는다."
  type        = string
  default     = "cron(35 9-15 ? * MON-FRI *)"
}

variable "schedule_state" {
  description = "ENABLED 또는 DISABLED."
  type        = string
  default     = "ENABLED"
  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.schedule_state)
    error_message = "schedule_state 는 ENABLED 또는 DISABLED 다."
  }
}
