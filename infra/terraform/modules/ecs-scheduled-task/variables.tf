variable "name" {
  description = "리소스 접두어 (예: edge-dev-data-pipeline). ECR·로그·SG·역할 이름에 사용"
  type        = string
}

variable "region" {
  description = "awslogs 및 ECR 리전"
  type        = string
}

variable "vpc_id" {
  description = "태스크 SG 가 속할 VPC"
  type        = string
}

variable "cluster_arn" {
  description = "태스크를 실행할 ECS 클러스터 ARN (보통 워커 클러스터)"
  type        = string
}

variable "subnet_ids" {
  description = "태스크 배치 서브넷(보통 private, NAT 경유 egress)"
  type        = list(string)
}

variable "schedules" {
  description = "스케줄 map. key 는 스케줄 이름 접미사, command 는 컨테이너 실행 명령 오버라이드"
  type = map(object({
    schedule_expression = string       # 예: cron(0 * * * ? *)
    command             = list(string) # 예: ["python", "-m", "data_pipeline.run", "ingest-raw"]
    enabled             = optional(bool, true)
  }))
}

variable "schedule_timezone" {
  description = "schedule_expression 해석 타임존"
  type        = string
  default     = "Asia/Seoul"
}

variable "environment" {
  description = "평문 환경변수 map"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "시크릿 환경변수 map (값은 Secrets Manager ARN, JSON 키는 arn:...:json-key:: 형식)"
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "실행 역할에 GetSecretValue 를 허용할 시크릿 base ARN 목록. 비우면 정책 없음"
  type        = list(string)
  default     = []
}

variable "task_policy_json" {
  description = "태스크 역할에 부착할 IAM 정책 JSON(예: 레이크 프리픽스 R/W). null=정책 없음"
  type        = string
  default     = null
}

variable "cpu" {
  description = "태스크 CPU 단위(256=0.25 vCPU)"
  type        = string
  default     = "256"
}

variable "memory" {
  description = "태스크 메모리(MiB)"
  type        = string
  default     = "512"
}

variable "cpu_architecture" {
  description = "X86_64 또는 ARM64 (push 하는 이미지 아키텍처와 일치해야 함)"
  type        = string
  default     = "X86_64"
}

variable "log_retention_days" {
  description = "CloudWatch 로그 보존일"
  type        = number
  default     = 14
}

variable "image_tag_mutability" {
  type    = string
  default = "IMMUTABLE"
}
