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
variable "host_count" {
  description = "Airflow 호스트 수. 1 = 가동, 0 = 호스트·서비스 중단 알람을 모두 내린다(검증 종료·중단). 콘솔로 내리면 다음 자동 apply 가 되돌린다"
  type        = number
  default     = 1
  validation {
    condition     = contains([0, 1], var.host_count)
    error_message = "host_count 는 0 또는 1."
  }
}

variable "host_until" {
  description = "호스트를 이 시각(RFC3339 UTC)에 내린다 — 스케줄러가 서비스 0·10분 뒤 호스트 0(default_stop.tf). 비우면 기한 없음. 호출부는 host_count 도 같은 시각으로 계산해 기한 뒤 apply 가 호스트를 되살리지 않게 한다"
  type        = string
  default     = ""
}

variable "task_memory" {
  description = "서비스 태스크의 합산 메모리 상한(MiB) — 세 구성요소와 자식 프로세스가 함께 쓴다. 호스트 등록 메모리보다 크면 배치되지 않는다"
  type        = number
  default     = 1024
}

variable "host_observer" {
  description = "검증 기간 호스트 관측기(systemd, host-observer.sh.tftpl) 설치. 상시 운영에서는 끈다"
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "Airflow 호스트 EC2 타입. arm64(Graviton) — ami_id 와 아키텍처가 같아야 한다. t4g.micro 는 로컬 사양 검증에서 불가(README)"
  type        = string
  default     = "t4g.small"
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

# 메타DB — 기존 업무 RDS 인스턴스 안의 전용 DB(db_name)·전용 역할(db_user). 비밀번호는 이 모듈의 앱 시크릿
# `meta_db_password`. DB·역할은 TF 가 아니라 검증 절차의 관리 태스크(verify/dbadmin.sh)가 만든다(README "메타DB").
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

variable "db_instance_identifier" {
  description = "업무 RDS 인스턴스 식별자 — 운영 중단 경보(stop.tf)가 FreeableMemory·SwapUsage 를 본다"
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

variable "master_db_secret_arn" {
  description = "업무 RDS 마스터 시크릿 — 관리 태스크(dbadmin)만 쓴다: 운영 메타DB·검증 전용 DB와 역할 생성·정리, 검증 원장 스키마 복제"
  type        = string
  default     = ""
}

variable "master_db_user" {
  type    = string
  default = ""
}

variable "business_db_name" {
  description = "검증 원장(edge_verify) 스키마의 복제 원본(pg_dump -s, 스키마만) — 업무 DB 이름"
  type        = string
  default     = ""
}

variable "kr_holidays" {
  type    = list(string)
  default = []
}

variable "verify_shutdown_at" {
  description = "검증 종료 시각(KST, 'YYYY-MM-DDTHH:MM:SS') — 새 제출 중단·grace 뒤 남은 검증 태스크 중단·호스트 0. 비우면 종료 장치 없음"
  type        = string
  default     = ""
}

variable "verify_hard_stop_at" {
  description = "종료 장치가 실패해도 서비스·호스트를 0 으로 만드는 시각(KST). verify_shutdown_at + grace 보다 뒤"
  type        = string
  default     = ""
}

variable "verify_shutdown_grace_seconds" {
  description = "종료 태스크가 검증 업무 태스크의 자연 종료를 기다리는 시간(초)"
  type        = number
  default     = 900
}
