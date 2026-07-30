variable "region" {
  description = "state 백엔드를 둘 region (envs/* 와 동일해야 함)"
  type        = string
  default     = "ap-northeast-2"
}

variable "state_bucket_name" {
  description = "Terraform state 를 둘 S3 버킷 이름(계정 전역 유일). 관례: edge-tfstate-<account-id>"
  type        = string
  default     = "edge-tfstate-393229433969"
}
