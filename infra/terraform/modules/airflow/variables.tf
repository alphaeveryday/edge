variable "name" {
  description = "리소스 이름 접두 (예: edge-dev-airflow)"
  type        = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "EC2 호스트·Airflow 태스크·검증 태스크를 둘 private 서브넷(NAT 경유 egress)"
  type        = list(string)
}

# ── EC2 용량 ────────────────────────────────────────────
variable "instance_type" {
  description = "Airflow 호스트 EC2 타입. arm64(Graviton) — ami_id 와 아키텍처가 같아야 한다"
  type        = string
  default     = "t4g.medium"
}

# 고정한다. SSM 공개 파라미터(recommended)를 data 로 읽으면 AMI 가 나올 때마다 launch template 이 바뀌고,
# 사람이 고르지 않은 시각(dev 머지 = apply)에 교체가 준비된다. 교체는 README "호스트 교체" 절차로 한다.
variable "ami_id" {
  description = "ECS 최적화 Amazon Linux 2023 arm64 AMI (/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended)"
  type        = string
}

variable "root_volume_gib" {
  description = "EC2 루트 EBS(gp3). 이미지 레이어·컨테이너 로그 캐시만 — 메타DB·로그 정본은 여기 없다"
  type        = number
  default     = 30
}

# ── Airflow ────────────────────────────────────────────
variable "image" {
  description = "Airflow 이미지 최초 URI(:태그). 서비스·마이그레이션 태스크 정의의 기준선일 뿐 — 실행 태그는 CD(deploy-airflow)가 소유한다"
  type        = string
}

variable "db_host" {
  type = string
}

variable "db_port" {
  type = number
}

variable "db_name" {
  type = string
}

variable "db_user" {
  type = string
}

variable "db_password_secret_arn" {
  description = "메타DB RDS 관리형 시크릿({username,password})"
  type        = string
}

variable "db_security_group_id" {
  description = "메타DB SG — Airflow·검증 태스크의 5432 인그레스를 여기 건다"
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_topic_arn" {
  description = "서비스 중단 알람을 보낼 SNS 토픽(파이프라인 알람과 같은 토픽)"
  type        = string
}

# ── 업무 ECS 태스크(장중 수급 레인) ─────────────────────
variable "batch_cluster_arn" {
  description = "업무 배치 태스크가 도는 클러스터(worker). EdgeStep 의 RunTask 대상"
  type        = string
}

variable "batch_task_definition_families" {
  description = "Airflow 가 띄울 수 있는 업무 태스크 정의 family(장중 수급: kis·bigkinds·rds·ops)"
  type        = list(string)
}

variable "batch_task_definition_prefix" {
  description = "EdgeStep 의 EDGE_ECS_TASKDEF_PREFIX(= data-pipeline 모듈 name)"
  type        = string
}

variable "batch_pass_role_arns" {
  description = "업무 태스크 정의의 역할(iam:PassRole 대상)"
  type        = list(string)
}

variable "batch_security_group_id" {
  description = "업무 태스크 SG(EDGE_ECS_SECURITY_GROUPS)"
  type        = string
}

variable "batch_log_group_name" {
  description = "업무 태스크 로그 그룹 — EdgeStep 이 컨테이너 로그를 task 로그로 끌어온다"
  type        = string
}

variable "batch_log_group_arn" {
  type = string
}

# ── 배포(CD) ─────────────────────────────────────────────
variable "deploy_role_name" {
  description = "GitHub Actions 배포 역할 이름 — 이미지 push·마이그레이션 태스크·서비스 롤링 권한을 덧붙인다"
  type        = string
}

variable "ecr_repository_arn" {
  type = string
}

# ── 격리 검증(ALPHA-1119 실제 AWS 검증) ──────────────────
variable "verify_enabled" {
  description = "격리 검증 자원(버킷·태스크 정의·역할) 생성. 운영 데이터·KIS 와 닿지 않는다"
  type        = bool
  default     = false
}

variable "verify_image" {
  description = "검증 태스크 이미지 — 배포된 data-pipeline 이미지 + 검증 shim(src/apps/cloud/airflow/verify)"
  type        = string
  default     = ""
}

variable "kr_holidays" {
  type    = list(string)
  default = []
}
