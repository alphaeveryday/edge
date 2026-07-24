variable "name" {
  description = "서비스 이름 (예: widget-api). 로그·Service Connect·SG 이름에 사용"
  type        = string
}

variable "region" {
  description = "awslogs 드라이버용 region"
  type        = string
}

variable "cluster_arn" {
  description = "배치할 ECS 클러스터 ARN"
  type        = string
}

variable "container_image" {
  description = "ECR 이미지 URI (예: <acct>.dkr.ecr.<region>.amazonaws.com/edge/super-admin-api:0.0.1)"
  type        = string
}

variable "container_port" {
  description = "컨테이너 수신 포트"
  type        = number
  default     = 8080
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
  description = "X86_64 또는 ARM64 (ECR 이미지 아키텍처와 일치해야 함)"
  type        = string
  default     = "X86_64"
}

variable "desired_count" {
  description = "실행 태스크 수"
  type        = number
  default     = 1
}

variable "vpc_id" {
  description = "서비스 SG 가 속할 VPC"
  type        = string
}

variable "subnet_ids" {
  description = "태스크 배치 서브넷(보통 private)"
  type        = list(string)
}

variable "assign_public_ip" {
  description = "public IP 부여 여부(private+NAT 면 false)"
  type        = bool
  default     = false
}

variable "ingress_security_group_ids" {
  description = "컨테이너 포트 인바운드를 허용할 SG 목록(예: gateway SG). 비우면 인바운드 없음"
  type        = list(string)
  default     = []
}

variable "ingress_cidr_blocks" {
  description = "컨테이너 포트 인바운드를 허용할 CIDR(테스트용). 평상시 비움"
  type        = list(string)
  default     = []
}

variable "service_connect_namespace_arn" {
  description = "Service Connect 네임스페이스 ARN"
  type        = string
}

variable "environment" {
  description = "평문 환경변수 map"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "시크릿 환경변수 map (값은 Secrets Manager/SSM ARN, JSON 키는 arn:...:json-key:: 형식)"
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "실행 역할에 GetSecretValue 를 허용할 시크릿 ARN 목록(secrets 로 주입하는 시크릿의 기반 ARN). 비우면 정책 없음"
  type        = list(string)
  default     = []
}

variable "target_group_arn" {
  description = "ALB 타깃그룹 ARN. 설정 시 서비스가 등록됨. null=LB 없음(내부 전용)"
  type        = string
  default     = null
}

variable "health_check_grace_period_seconds" {
  description = "LB 헬스체크 유예(기동 느린 JVM 대비). target_group_arn 있을 때만 적용"
  type        = number
  default     = 120
}

variable "health_check_command" {
  description = "컨테이너 헬스체크 명령(예: [\"CMD-SHELL\", \"curl -f localhost:8080/actuator/health || exit 1\"]). 이미지에 curl 필요. null=미설정"
  type        = list(string)
  default     = null
}

variable "log_retention_days" {
  description = "CloudWatch 로그 보존일"
  type        = number
  default     = 14
}

variable "enable_execute_command" {
  description = "ECS Exec(컨테이너 디버그 접속) 활성 여부"
  type        = bool
  default     = false
}
