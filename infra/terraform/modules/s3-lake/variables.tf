variable "bucket_name" {
  description = "레이크 버킷 이름 (S3 버킷 이름은 전역 유일 — 선점돼 있으면 바꿔야 한다)"
  type        = string
}

variable "raw_glacier_days" {
  description = "raw/ 프리픽스를 Glacier 로 내리는 경과일"
  type        = number
  default     = 90
}

variable "raw_expiration_days" {
  description = "raw/ 프리픽스 객체 만료 경과일 (canonical 이 정본이므로 raw 는 유한 보존)"
  type        = number
  default     = 365
}

variable "raw_noncurrent_grace_days" {
  description = "만료(delete marker) 후 noncurrent 버전을 실제 삭제하기까지 유예일. 실보존 ≈ raw_expiration_days + 이 값"
  type        = number
  default     = 30
}
