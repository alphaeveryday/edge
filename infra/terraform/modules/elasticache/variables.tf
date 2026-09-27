variable "name" {
  description = "리소스 이름 접두(예: edge-dev-app). replication group id·서브넷그룹·SG 이름에 사용"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "서브넷 그룹에 넣을 서브넷(data tier, 최소 2 AZ)"
  type        = list(string)
}

variable "node_type" {
  description = "노드 클래스 (dev: cache.t4g.micro)"
  type        = string
  default     = "cache.t4g.micro"
}

variable "engine_version" {
  description = "Redis 엔진 버전"
  type        = string
  default     = "7.1"
}

variable "parameter_group_name" {
  description = "파라미터 그룹 (클러스터 모드 끔)"
  type        = string
  default     = "default.redis7"
}

variable "num_cache_clusters" {
  description = "노드 수 (프라이머리 1 + 레플리카). 2 이상이면 Multi-AZ 자동 페일오버"
  type        = number
  default     = 2
}

variable "snapshot_retention_limit" {
  description = "스냅샷 보존일 (0=비활성). 투표 캐시는 DB 에서 재조정되므로 dev 는 0"
  type        = number
  default     = 0
}
