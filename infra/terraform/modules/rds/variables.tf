variable "name" {
  description = "리소스 이름/식별자 (예: edge-dev). DB 인스턴스 identifier·서브넷그룹·SG 이름에 사용"
  type        = string
}

variable "vpc_id" {
  description = "RDS SG 가 속할 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "DB 서브넷 그룹에 넣을 서브넷(private, 최소 2 AZ)"
  type        = list(string)
}

variable "db_name" {
  description = "생성할 초기 DB 이름 (앱·Flyway 규약과 일치)"
  type        = string
  default     = "edge"
}

variable "master_username" {
  description = "마스터 유저명 (앱·Flyway 규약과 일치). 비밀번호는 Secrets Manager 가 관리"
  type        = string
  default     = "edge"
}

variable "engine_version" {
  description = "PostgreSQL 엔진 버전. 존재하지 않는 마이너면 apply 실패 → 필요 시 조정"
  type        = string
  default     = "16.8"
}

variable "instance_class" {
  description = "인스턴스 클래스 (dev: db.t4g.micro)"
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "스토리지(GiB)"
  type        = number
  default     = 20
}

variable "multi_az" {
  description = "Multi-AZ 여부 (dev: false)"
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "자동 백업 보존일 (0=백업 비활성)"
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "삭제 보호 (dev: false, prod: true 권장)"
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "삭제 시 최종 스냅샷 생략 (dev: true)"
  type        = bool
  default     = true
}
