variable "name" {
  description = "리소스 이름 접두어 (예: edge-dev-worker)"
  type        = string
}

variable "region" {
  description = "awslogs 드라이버에 넘길 region"
  type        = string
}

variable "vpc_id" {
  description = "태스크 보안그룹을 만들 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "태스크를 띄울 서브넷. NAT 비용 회피 경로면 퍼블릭 서브넷 + assign_public_ip=true"
  type        = list(string)
}

variable "assign_public_ip" {
  description = "태스크에 퍼블릭 IP 할당 여부. 퍼블릭 서브넷에서 NAT 없이 아웃바운드하려면 true"
  type        = bool
  default     = true
}

variable "container_image" {
  description = "파이프라인 컨테이너 이미지 URI(:태그 포함). 두 태스크 정의가 공유"
  type        = string
}

variable "cpu_architecture" {
  description = "Fargate CPU 아키텍처 (ECR 이미지와 일치해야 함)"
  type        = string
  default     = "X86_64"
}

variable "container_insights" {
  description = "CloudWatch Container Insights 활성 여부"
  type        = bool
  default     = true
}

variable "pipeline_cpu" {
  description = "일반 단계 태스크 vCPU (1024 = 1 vCPU)"
  type        = string
  default     = "2048"
}

variable "pipeline_memory" {
  description = "일반 단계 태스크 메모리(MiB)"
  type        = string
  default     = "4096"
}

variable "inference_cpu" {
  description = "추론 단계 태스크 vCPU"
  type        = string
  default     = "4096"
}

variable "inference_memory" {
  description = "추론 단계 태스크 메모리(MiB)"
  type        = string
  default     = "16384"
}

variable "environment" {
  description = "두 태스크 정의 공통 평문 환경변수 (이름→값)"
  type        = map(string)
  default     = {}
}

variable "inference_environment" {
  description = "추론 태스크 정의에만 추가되는 환경변수 (모델 캐시·LLM 설정 등)"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "두 태스크 정의 공통 주입 시크릿 (환경변수 이름→Secrets Manager valueFrom)"
  type        = map(string)
  default     = {}
}

variable "inference_secrets" {
  description = "추론 태스크 정의에만 주입되는 시크릿"
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "실행 역할이 GetSecretValue 할 시크릿 base ARN 목록 (secrets·inference_secrets 가 참조하는 것 전부)"
  type        = list(string)
  default     = []
}

variable "runtime_secret_arns" {
  description = "컨테이너 앱이 런타임에 직접 읽는 시크릿 ARN 목록 (태스크 역할 GetSecretValue, 예: DB 자격증명)"
  type        = list(string)
  default     = []
}

variable "s3_bucket_arns" {
  description = "태스크 역할이 읽기/쓰기할 S3 버킷 ARN 목록 (오브젝트 권한은 /* 로 확장)"
  type        = list(string)
  default     = []
}

variable "steps" {
  description = "파이프라인 단계 목록(실행 순서대로). command 가 컨테이너 Command 이자 상태 이름(Step-<command>)이 된다. inference=true 면 추론 태스크 정의 사용"
  type = list(object({
    command   = string
    inference = optional(bool, false)
  }))

  validation {
    condition     = length(var.steps) > 0
    error_message = "steps 는 1개 이상이어야 한다."
  }
}

variable "sfn_timeout_seconds" {
  description = "상태 머신 전체 실행 타임아웃(초)"
  type        = number
  default     = 86400
}

variable "schedule_expression" {
  description = "EventBridge Scheduler cron 식"
  type        = string
  default     = "cron(5 16 ? * MON-FRI *)" # 뉴욕 장 마감(16:00 ET) 5분 뒤, 평일만
}

variable "schedule_timezone" {
  description = "cron 식의 타임존"
  type        = string
  default     = "America/New_York"
}

variable "schedule_enabled" {
  description = "스케줄 활성 여부. 구 스택과 병행 운영 중에는 false 로 두고 컷오버 때 켠다"
  type        = bool
  default     = false
}

variable "run_mode" {
  description = "스케줄 실행이 상태 머신 입력으로 넘길 mode 값"
  type        = string
  default     = "incremental"
}

variable "log_retention_days" {
  description = "CloudWatch 로그 보존 기간(일)"
  type        = number
  default     = 30
}
