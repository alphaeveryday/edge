variable "name" {
  description = "클러스터 이름 (예: edge-dev-service)"
  type        = string
}

variable "namespace_name" {
  description = "Service Connect 내부 네임스페이스 (예: edge.internal)"
  type        = string
}

variable "container_insights" {
  description = "CloudWatch Container Insights 활성 여부"
  type        = bool
  default     = true
}
